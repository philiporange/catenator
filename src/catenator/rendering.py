"""Render factual project context using whole sections and a strict budget.

The renderer works from a single discovery snapshot. It reserves room for a
bounded overview, tree, and coverage report, then gives each main directory an
outline before upgrading important files. Token costs are cached per section;
only the final assembled document needs an exact aggregate check. Optional Jev
scores select ignored, summarized, or verbatim files and prioritize their
expansion. Source is never executed, and AI summaries require explicit opt-in.
"""

import re
from collections import Counter

from . import summarizer
from .project_summary import build_project_overview


def _fence(content):
    runs = re.findall(r"`+", content)
    return "`" * max(3, max((len(run) + 1 for run in runs), default=3))


def _file_block(path, content, label="full"):
    marker = "" if label == "full" else f" ({label})"
    fence = _fence(content)
    return f"# {path}{marker}\n{fence}\n{content.rstrip()}\n{fence}\n\n"


def _fit_lines(heading, lines, budget, count, fenced=False):
    """Keep complete lines and account for fences and the omission marker."""
    if budget <= 0:
        return ""
    kept = []

    def render(values):
        body = "\n".join(values)
        if fenced:
            fence = _fence(body)
            body = f"{fence}\n{body}\n{fence}"
        return f"# {heading}\n{body}\n\n"

    for line in lines:
        if (
            count(render(kept + [line, "... (more entries omitted)"]))
            <= budget
        ):
            kept.append(line)
        else:
            # A long bullet must not crowd out shorter, useful facts.
            continue
    if len(kept) < len(lines):
        kept.append("... (more entries omitted)")
    result = render(kept) if kept else ""
    return result if count(result) <= budget else ""


def _coverage_order(ranked):
    """Represent each source directory before taking its additional files."""
    first, rest, seen = [], [], set()
    for record in ranked:
        path = record[0].replace("\\", "/")
        group = path.rsplit("/", 1)[0] if "/" in path else "."
        if group in seen:
            rest.append(record)
        else:
            seen.add(group)
            first.append(record)
    return first + rest


def _outline(path, summary, budget, count):
    """Prefer declarations to lengthy imports when producing a tiny outline."""
    lines = summary.splitlines()
    declarations = [
        line
        for line in lines
        if re.search(r"\b(class|def|function|interface|export|enum)\s", line)
    ]
    public = [line for line in declarations if not re.search(r"\bdef _", line)]
    candidates = lines[:2] + public + declarations + lines[2:]
    kept, seen = [], set()
    for line in candidates:
        if line in seen:
            continue
        seen.add(line)
        candidate = _file_block(
            path, "\n".join(kept + [line, "... (outline)"]), "outline"
        )
        if count(candidate) <= budget:
            kept.append(line)
    if not kept:
        return ""
    return _file_block(path, "\n".join(kept + ["... (outline)"]), "outline")


def render_project(
    cat,
    files,
    overrides=None,
    token_limit=None,
    use_llm=False,
    file_scores=None,
):
    """Build one output document and record exact inclusion counts on *cat*."""
    overrides = overrides or {}
    records, labels = [], {}
    for path, absolute, content in files:
        label = "full"
        if path in overrides:
            replacement = overrides[path]
            content, label = (
                replacement
                if isinstance(replacement, tuple)
                else (replacement, "summary")
            )
        if content is not None:
            records.append((path, absolute, content))
            labels[path] = label

    if file_scores is None:
        ranked = summarizer.rank_files_by_importance(cat.directory, records)
    else:
        ranked = sorted(
            [
                (*record, file_scores[record[0]])
                for record in records
                if file_scores[record[0]] >= 0.5
            ],
            key=lambda record: (-record[3], record[0]),
        )
    title = f"### {cat.title}\n\n"
    overview_lines = (
        build_project_overview(records) if cat.include_overview else []
    )
    tree_lines = (
        cat.generate_directory_tree(cat._tree_paths).splitlines()
        if cat.include_tree
        else []
    )
    selected = {}
    total = len(files)

    def report():
        counts = Counter(
            label if label in {"full", "outline"} else "summary"
            for _, label in selected.values()
        )
        omitted = total - len(selected)
        cat.last_report.update(
            eligible=total,
            full=counts["full"],
            summary=counts["summary"],
            outline=counts["outline"],
            omitted=omitted,
        )
        return (
            "# Coverage\n"
            f"{total} eligible files: {counts['full']} full, "
            f"{counts['summary']} summaries, {counts['outline']} outlines, "
            f"{omitted} omitted. "
            f"Skipped: {cat.last_report.get('unreadable', 0)} unreadable, "
            f"{cat.last_report.get('minified', 0)} generated/minified.\n\n"
        )

    full_blocks = {
        path: _file_block(path, content, labels[path])
        for path, _, content, _ in ranked
    }
    if file_scores is not None:
        for path, absolute, content, score in ranked:
            if score >= 1.5 or path in overrides:
                continue
            summary = (
                summarizer.summarize_file(
                    cat.directory, path, absolute, content, use_llm=True
                )
                if use_llm
                else summarizer.extract_signatures(content, path)
            )
            full_blocks[path] = _file_block(path, summary, "summary")
            labels[path] = "summary"
    if token_limit is None:
        selected.update(
            {
                path: (block, labels[path])
                for path, block in full_blocks.items()
            }
        )
        overview = (
            "# Project Overview\n" + "\n".join(overview_lines) + "\n\n"
            if overview_lines
            else ""
        )
        tree = (
            "# Project Directory Structure\n"
            + _fence("\n".join(tree_lines))
            + "\n"
            + "\n".join(tree_lines)
            + "\n"
            + _fence("\n".join(tree_lines))
            + "\n\n"
            if tree_lines
            else ""
        )
        return (
            title + overview + report() + tree + "".join(full_blocks.values())
        )

    costs = {}

    def count(text):
        if text not in costs:
            costs[text] = cat.count_tokens(text)
        return costs[text]

    if count(title) > token_limit:
        title = (
            "# Project\n\n" if count("# Project\n\n") <= token_limit else ""
        )
    # Reserve worst-case counter widths, independent of the selection.
    coverage_reserve = count(report()) + len(str(total)) * 8
    if count(title) + coverage_reserve > token_limit:
        cat.last_report.update(omitted=total)
        return title
    overview = (
        _fit_lines(
            "Project Overview",
            overview_lines,
            min(1500, token_limit // 4),
            count,
        )
        if overview_lines
        else ""
    )
    tree = (
        _fit_lines(
            "Project Directory Structure",
            tree_lines,
            min(700, token_limit // 10),
            count,
            fenced=True,
        )
        if tree_lines
        else ""
    )
    available = (
        token_limit
        - count(title)
        - count(overview)
        - count(tree)
        - coverage_reserve
    )
    if available < 0:
        overview, tree = "", ""
        available = token_limit - count(title) - coverage_reserve

    if sum(count(block) for block in full_blocks.values()) <= available:
        selected.update(
            {
                path: (block, labels[path])
                for path, block in full_blocks.items()
            }
        )
    else:
        summaries = {}
        initial_budget = max(65, min(180, available // max(1, len(records))))
        order = _coverage_order(ranked) if file_scores is None else ranked
        for path, absolute, content, _score in order:
            if available <= 0:
                break
            full = full_blocks[path]
            if count(full) <= min(initial_budget, available):
                block, label = full, labels[path]
            else:
                if path in overrides:
                    summary = content
                elif use_llm:
                    summary = summarizer.summarize_file(
                        cat.directory, path, absolute, content, use_llm=True
                    )
                else:
                    summary = summarizer.extract_signatures(content, path)
                summaries[path] = _file_block(path, summary, "summary")
                if count(summaries[path]) <= min(initial_budget, available):
                    block, label = summaries[path], "summary"
                else:
                    block = _outline(
                        path, summary, min(initial_budget, available), count
                    )
                    label = "outline"
            if block and count(block) <= available:
                selected[path] = (block, label)
                available -= count(block)

        # Expand important files without displacing the other directories.
        for path, _absolute, _content, _score in ranked:
            old_cost = count(selected[path][0]) if path in selected else 0
            variants = [(full_blocks[path], labels[path])]
            if path in summaries:
                variants.append((summaries[path], "summary"))
            for block, label in variants:
                extra = count(block) - old_cost
                if extra <= available:
                    selected[path] = (block, label)
                    available -= extra
                    break

    def assemble():
        return (
            title
            + overview
            + report()
            + tree
            + "".join(
                selected[path][0] for path, *_ in ranked if path in selected
            )
        )

    output = assemble()
    # BPE boundaries can differ from the sum of independently counted sections.
    # Remove whole low-priority sections if the final encoding requires it.
    for path, *_ in reversed(ranked):
        if count(output) <= token_limit:
            break
        selected.pop(path, None)
        output = assemble()
    if count(output) > token_limit:
        tree = ""
        output = assemble()
    if count(output) > token_limit:
        overview = ""
        output = assemble()
    return output if count(output) <= token_limit else title
