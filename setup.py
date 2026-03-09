from pathlib import Path

from setuptools import find_packages, setup


README = Path(__file__).with_name("README.md").read_text(encoding="utf-8")


setup(
    name="boss-ai",
    version="0.1.0",
    description="Builder Orchestration System for Sagau",
    long_description=README,
    long_description_content_type="text/markdown",
    python_requires=">=3.9",
    packages=find_packages(include=["boss*", "cli*"]),
    include_package_data=True,
    package_data={"boss": ["prompts/*.txt", "web/ui/*"]},
    install_requires=[
        "openai",
        "anthropic",
        "langchain",
        "langgraph",
        "rich",
        "typer",
        "tqdm",
        "watchdog",
        "python-dotenv",
        "PyYAML",
        "fastapi",
        "uvicorn",
        "SpeechRecognition",
    ],
    entry_points={"console_scripts": ["boss=cli.boss_cli:run"]},
)
