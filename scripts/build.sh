#!/bin/bash

# Paths
SCRIPTS_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )"
PROJECT_DIR="$(dirname $SCRIPTS_DIR)"
MODULE_NAME="$(basename $PROJECT_DIR)"
MODULE_DIR="$PROJECT_DIR/src/$MODULE_NAME"
TESTS_DIR="$PROJECT_DIR/tests"

BLACK=black
BLACK_OPTS="--line-length 79"
LINTER=/usr/bin/flake8
GITIGNORE_URL="https://raw.githubusercontent.com/github/gitignore/main/Python.gitignore"

# Update .gitignore if older than 30 days
GITIGNORE_PATH="$PROJECT_DIR/.gitignore"
if [ ! -f $GITIGNORE_PATH ] || [ `find $GITIGNORE_PATH -mtime +30` ]; then
    echo "Updating .gitignore" 
    wget -O $GITIGNORE_PATH $GITIGNORE_URL 2>/dev/null
fi
# Don't allow cats in the repo
if ! grep -q "cat.md" $GITIGNORE_PATH; then
    echo "cat.md" >> $GITIGNORE_PATH
fi

# Run black
echo "Running black"
$BLACK $BLACK_OPTS $MODULE_DIR/*.py $TESTS_DIR/*.py

# Run linter
echo "Running linter"
$LINTER $MODULE_DIR/*.py
if [ $? -ne 0 ]; then
    echo "Linting failed"
    exit 1
fi
echo "Linting passed"

# Run tests
echo "Running tests"
python3 -m unittest discover -s $TESTS_DIR
if [ $? -ne 0 ]; then
    echo "Tests failed"
    exit 1
fi

# Clean up
echo "Cleaning up"
find . -type d -name '__pycache__' -exec rm -r {} +
find . -type d -name '*.egg-info' -exec rm -r {} +
# Sorry, no cats allowed
find . -type f -name 'cat.md' -exec rm {} +

# Ask to continue building package
read -p "Build and upload? (y/n) " -n 1 -r
echo
if [[ ! $REPLY =~ ^[Yy]$ ]]; then
    echo "Build aborted"
    exit 1
fi

# Build package
echo "Building package"
python3 setup.py sdist bdist_wheel
if [ $? -ne 0 ]; then
    echo "Build failed"
    exit 1
fi

# Upload package
echo "Uploading package"
twine upload dist/*

echo "Build successful"

exit 0
