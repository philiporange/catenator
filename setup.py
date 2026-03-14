import re
from setuptools import setup, find_packages


def get_metadata():
    """Read metadata from __init__.py without importing the package."""
    with open("src/catenator/__init__.py", "r", encoding="utf-8") as f:
        content = f.read()
    metadata = {}
    for key in ["__version__", "__project__", "__author__", "__email__", "__description__"]:
        match = re.search(rf'^{key}\s*=\s*["\']([^"\']+)["\']', content, re.MULTILINE)
        if match:
            metadata[key] = match.group(1)
    return metadata


metadata = get_metadata()
__version__ = metadata["__version__"]
__project__ = metadata["__project__"]
__author__ = metadata["__author__"]
__email__ = metadata["__email__"]
__description__ = metadata["__description__"]

with open("README.md", "r", encoding="utf-8") as fh:
    long_description = fh.read()

setup(
    name=__project__,
    version=__version__,
    author=__author__,
    author_email=__email__,
    description=__description__,
    long_description=long_description,
    long_description_content_type="text/markdown",
    url="https://github.com/philiporange/catenator",
    packages=find_packages(where="src"),
    package_dir={"": "src"},
    install_requires=[
        "pyperclip",
        "watchdog",
        "pyyaml",
    ],
    extras_require={
        "token_counting": ["tiktoken"],
        "summarize": ["tiktoken", "robot"],
    },
    entry_points={
        "console_scripts": [
            "catenator=catenator.catenator:main",
        ],
    },
    classifiers=[
        "Development Status :: 3 - Alpha",
        "Intended Audience :: Developers",
        "License :: CC0 1.0 Universal (CC0 1.0) Public Domain Dedication",
        "Operating System :: OS Independent",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.6",
        "Programming Language :: Python :: 3.7",
        "Programming Language :: Python :: 3.8",
        "Programming Language :: Python :: 3.9",
    ],
    python_requires=">=3.6",
)
