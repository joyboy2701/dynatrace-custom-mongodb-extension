from setuptools import setup, find_packages

setup(
    name="mongodb_custom_query",
    version="0.1.39",
    description="MongoDB Custom Query Dynatrace Extension",
    packages=find_packages(),
    python_requires=">=3.10",
    include_package_data=True,
    install_requires=[
        "dt-extensions-sdk",
        "pymongo>=4.6,<5.0",
    ],
    extras_require={"dev": ["dt-extensions-sdk[cli]"]},
)
