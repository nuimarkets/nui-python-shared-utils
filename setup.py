"""
Setup for nui-python-shared-utils package.
"""

from setuptools import setup, find_packages

with open("README.md", "r", encoding="utf-8") as fh:
    long_description = fh.read()

setup(
    name="nui-python-shared-utils",
    use_scm_version=True,
    setup_requires=["setuptools-scm>=8.0"],
    author="NUI Markets",
    author_email="develop@nuimarkets.com",
    description="Shared Python utilities for AWS Lambda, CLI tools, and agents with Slack, Elasticsearch, and monitoring integrations",
    long_description=long_description,
    long_description_content_type="text/markdown",
    url="https://github.com/nuimarkets/nui-python-shared-utils",
    project_urls={
        "Bug Tracker": "https://github.com/nuimarkets/nui-python-shared-utils/issues",
        "Documentation": "https://github.com/nuimarkets/nui-python-shared-utils/blob/main/README.md",
        "Source": "https://github.com/nuimarkets/nui-python-shared-utils",
    },
    classifiers=[
        "Development Status :: 4 - Beta",
        "Intended Audience :: Developers",
        "License :: OSI Approved :: MIT License",
        "Operating System :: OS Independent",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.10",
        "Programming Language :: Python :: 3.11",
        "Programming Language :: Python :: 3.12",
        "Topic :: Software Development :: Libraries :: Python Modules",
        "Topic :: System :: Monitoring",
        "Topic :: Internet :: WWW/HTTP :: Dynamic Content",
        "Environment :: Console",
        "Framework :: AWS CDK",
    ],
    packages=find_packages(),
    package_data={
        "nui_shared_utils": ["slack_setup/*.yaml"],
    },
    install_requires=[
        "boto3>=1.20.0",
        "click>=8.0.0",
        "pyyaml>=6.0",
        # Zone data for stdlib `zoneinfo`, which otherwise reads the system IANA
        # database. Windows ships none; POSIX systems do, so they pay nothing.
        "tzdata>=2023.3; platform_system == 'Windows'",
    ],
    extras_require={
        # Client majors 7.17, 8.x and 9.x are all supported: es_client sends one
        # call shape that is valid on each (see docs/decisions/ADR-007). The <10
        # ceiling is the untested next major, not a known incompatibility.
        "elasticsearch": ["elasticsearch>=7.17.0,<10.0.0"],
        "database": ["pymysql>=1.0.0", "psycopg2-binary>=2.9.0"],
        "slack": ["slack-sdk>=3.19.0"],
        "powertools": [
            "aws-lambda-powertools>=3.6.0,<4.0.0",
            "coloredlogs>=15.0",
        ],
        "jwt": ["rsa>=4.9"],
        # Zone data for images that carry no system IANA database (Alpine,
        # distroless). Not expressible as an environment marker, hence an
        # opt-in extra. Unneeded on Linux/macOS or an AWS Lambda runtime.
        "timezone": ["tzdata>=2023.3"],
        # Pinned to the published PyPI release (no direct-URL deps, PyPI rejects
        # those on upload). Kept out of "all": opt-in.
        "snowflake": [
            "snowflake-sql-api>=0.1.1,<0.2.0",
        ],
        # Anthropic (Claude) helper. anthropic[bedrock] covers both auth modes
        # (API key + Bedrock IAM). Kept out of "all" (same as snowflake): the
        # SDK is specialized and opt-in. Keep in sync with pyproject.toml.
        "llm": ["anthropic[bedrock]>=0.45.0,<1.0.0"],
        "all": [
            "elasticsearch>=7.17.0,<10.0.0",
            "pymysql>=1.0.0",
            "psycopg2-binary>=2.9.0",
            "slack-sdk>=3.19.0",
            "aws-lambda-powertools>=3.6.0,<4.0.0",
            "coloredlogs>=15.0",
            "rsa>=4.9",
        ],
        "dev": [
            "pytest>=7.0.0",
            "pytest-cov>=4.0.0",
            "pytest-mock>=3.10.0",
            "moto>=4.0.0",
            "black>=22.0.0",
            "mypy>=0.990",
            "boto3-stubs[essential]>=1.20.0",
            "types-PyYAML>=6.0.0",
            "twine>=4.0.0",
            "build>=0.8.0",
            "rsa>=4.9",
            "cryptography>=41.0.0",
            # Lets the test suite exercise the zone-data fallback on a host that
            # has a system IANA database; see the "timezone" extra.
            "tzdata>=2023.3",
            "anthropic[bedrock]>=0.45.0,<1.0.0",
        ],
    },
    python_requires=">=3.10",
    keywords="aws lambda utilities slack elasticsearch monitoring serverless python shared",
)
