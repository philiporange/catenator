# Catenator

Catenator is a Python tool for concatenating code files in a directory into a single output string.

## Features

- Concatenate code files from a specified directory
- Include or exclude specific file extensions
- Include a directory tree structure
- Include README files in the output
- Output to file, clipboard, or stdout
- gitignore-style .catignore files

## Installation

Install using pip
   ```
   pip install catenator
   ```

## Usage

### As a Command-Line Tool

Basic usage:
```
catenator /path/to/your/project
```

Options:
- `--output FILE`: Write output to a file instead of stdout
- `--clipboard`: Copy output to clipboard
- `--no-tree`: Disable directory tree generation
- `--no-readme`: Exclude README files from the output
- `--include EXTENSIONS`: Comma-separated list of file extensions to include (replaces defaults)
- `--ignore EXTENSIONS`: Comma-separated list of file extensions to ignore
- `--count-tokens`: Output approximation of how many tokens in output (tiktoken cl100k_base)
- `--watch`: Watch for changes and update output file automatically (requires --output)
- `--ignore-tests`: Leave out tests from the concatenated output


Example:
```
python catenator.py /path/to/your/project --output concatenated.md --include py,js,ts
```

### As a Python Module

You can also use Catenator in your Python scripts:

```python
from catenator import Catenator

catenator = Catenator(
    directory='/path/to/your/project',
    include_extensions=['py', 'js', 'ts'],
)
result = catenator.catenate()
print(result)
```

## .catignore File

The .catignore file allows you to specify files and directories that should be excluded from the concatenation process. The syntax is like .gitignore files.

### Syntax

Lines starting with # are treated as comments.
Blank lines are ignored.
Patterns can include filenames, directories, or wildcard characters.

### Examples

```
# Ignore all JavaScript files
*.js

# Ignore specific file
ignored_file.txt

# Ignore entire directory
ignored_dir/
```

## .catconfig.yaml for Custom Builds

For more complex configurations, you can define custom "builds" in a `.catconfig.yaml` file in your project's root directory. This allows you to specify multiple sets of whitelisted and blacklisted files.

### `--build` Option

To use a build, use the `--build` command-line option:
```
catenator /path/to/your/project --build <build_name>
```

When you use the `--build` option, the catenator will ignore `.catignore` and other filtering flags, and will instead rely solely on the `whitelist` and `blacklist` defined in the specified build.

### Example `.catconfig.yaml`

Here is an example of a `.catconfig.yaml` file with two builds, `frontend` and `backend`:
```yaml
builds:
  frontend:
    whitelist:
      - "src/frontend/"
      - "README.md"
    blacklist:
      - "src/frontend/node_modules/"
  backend:
    whitelist:
      - "src/backend/"
      - "requirements.txt"
    blacklist:
      - "*.log"
```

In this example:
- `catenator . --build frontend` will concatenate all files in `src/frontend/` (except `node_modules`) and the `README.md` file.
- `catenator . --build backend` will concatenate all files in `src/backend/` and the `requirements.txt` file, excluding any `.log` files.

## License

This project is licensed under the Creative Commons Zero v1.0 Universal (CC0-1.0) License.