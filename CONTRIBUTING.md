# Contributing to NUI Python Shared Utilities

We welcome contributions to the NUI Python Shared Utilities project! This document provides guidelines for contributing.

## Getting Started

### Prerequisites

- Python 3.10 or higher
- Git
- Virtual environment tool (venv, virtualenv, etc.)

### Development Setup

1. Fork the repository on GitHub
2. Clone your fork locally:

   ```bash
   git clone https://github.com/your-username/nui-python-shared-utils.git
   cd nui-python-shared-utils
   ```

3. Create a virtual environment:

   ```bash
   python -m venv venv
   source venv/bin/activate  # On Windows: venv\Scripts\activate
   ```

4. Install development dependencies:

   ```bash
   pip install -e .[dev]
   ```

## Development Workflow

### Running Tests

```bash
# Run all tests
pytest

# Run tests with coverage
pytest --cov=nui_shared_utils --cov-report=html

# Run specific test categories
pytest -m unit      # Unit tests only
pytest -m integration  # Integration tests (requires AWS access)
```

### Code Quality

We use several tools to maintain code quality:

```bash
# Format code
black nui_shared_utils tests

# Type checking
mypy nui_shared_utils

# Run all quality checks
make lint  # If Makefile exists
```

### Code Style

- Follow PEP 8 style guidelines
- Use Black for code formatting (line length: 88 characters)
- Add type hints where appropriate
- Write docstrings for all public functions and classes
- Keep functions small and focused

## Contributing Guidelines

### Submitting Changes

1. Create a new branch for your feature/fix:

   ```bash
   git checkout -b feature/your-feature-name
   ```

2. Make your changes and ensure:
   - All tests pass
   - Code is properly formatted
   - New functionality includes tests
   - Documentation is updated

3. Commit your changes:

   ```bash
   git commit -m "Add: brief description of your changes"
   ```

4. Push to your fork:

   ```bash
   git push origin feature/your-feature-name
   ```

5. Create a Pull Request on GitHub

### Pull Request Guidelines

- **Title**: Use a clear, descriptive title
- **Description**: Explain what your changes do and why
- **Testing**: Include information about how you tested your changes

### Commit Message Format

Use conventional commits format:

- `feat:` for new features
- `fix:` for bug fixes
- `docs:` for documentation changes
- `test:` for test additions/changes
- `refactor:` for code refactoring
- `chore:` for maintenance tasks

Examples:

- `feat: add retry decorator for database operations`
- `fix: handle missing environment variables gracefully`
- `docs: update configuration examples in README`

## Adding New Utilities

When adding new utilities to the package:

1. **Create the module** in the `nui_shared_utils/` directory
2. **Add comprehensive tests** in the `tests/` directory
3. **Update `__init__.py`** to export new functions/classes
4. **Update documentation** with usage examples
5. **Consider backward compatibility** - don't break existing APIs
6. **Add optional dependencies** to `pyproject.toml` if needed

### Example New Utility Structure

```python
# nui_shared_utils/my_utility.py
"""
My utility for doing something useful.
"""

from typing import Dict, Optional
import logging

log = logging.getLogger(__name__)

class MyUtility:
    """A utility that does something useful."""
    
    def __init__(self, config: Optional[Dict] = None):
        """Initialize the utility."""
        self.config = config or {}
    
    def do_something(self, data: str) -> str:
        """
        Process some data.
        
        Args:
            data: Input data to process
            
        Returns:
            Processed data
            
        Raises:
            ValueError: If data is invalid
        """
        if not data:
            raise ValueError("Data cannot be empty")
        
        # Implementation here
        return f"processed: {data}"
```

## Testing Guidelines

### Test Structure

- **Unit tests**: Test individual functions/methods in isolation
- **Integration tests**: Test interactions with external services
- **Mock external dependencies**: Use `unittest.mock` for AWS services, etc.

### Test Naming

- Use descriptive test names that explain what is being tested
- Group related tests in classes
- Use `test_<functionality>_<condition>_<expected_result>` pattern

Example:

```python
class TestSlackClient:
    def test_send_message_success_returns_true(self):
        """Test that send_message returns True on successful API call."""
        # Test implementation
```

### AWS Service Mocking

When testing code that interacts with AWS services:

```python
from moto import mock_secretsmanager
from unittest.mock import patch

@mock_secretsmanager
def test_secret_retrieval():
    """Test secret retrieval from mocked AWS Secrets Manager."""
    # Use moto to mock AWS services
    # Test your code
```

## Documentation

### Docstring Format

Use Google-style docstrings:

```python
def example_function(param1: str, param2: Optional[int] = None) -> Dict[str, Any]:
    """
    Brief description of what the function does.
    
    More detailed description if needed. Explain the purpose,
    behavior, and any important notes.
    
    Args:
        param1: Description of the first parameter
        param2: Description of the optional second parameter
        
    Returns:
        Description of what the function returns
        
    Raises:
        ValueError: When param1 is empty
        ConnectionError: When unable to connect to service
        
    Example:
        >>> result = example_function("hello")
        >>> print(result)
        {'status': 'success', 'data': 'hello'}
    """
```

### README Updates

When adding new functionality, update the README.md with:

- Usage examples
- Configuration options
- Any new dependencies

## Issue Reporting

When reporting issues:

1. **Search existing issues** first
2. **Use a clear title** that describes the problem
3. **Provide details**:
   - Python version
   - Package version
   - Steps to reproduce
   - Expected vs. actual behavior
   - Error messages/stack traces
4. **Minimal reproduction case** if possible

## Community Guidelines

- Be respectful and constructive in discussions
- Help others learn and grow
- Focus on the code and ideas, not the person
- When in doubt, ask questions

## License

By contributing to this project, you agree that your contributions will be licensed under the MIT License.

## Questions?

If you have questions about contributing, please:

- Open an issue with the "question" label
- Check the existing documentation
- Look at similar implementations in the codebase

Thank you for contributing! 🎉
