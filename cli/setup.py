"""Setup script for SMSLY Hosting CLI."""
from setuptools import setup

setup(
    name='smsly-cli',
    version='1.0.0',
    py_modules=['smsly_cli'],
    install_requires=['requests>=2.28.0'],
    entry_points={
        'console_scripts': [
            'smsly=smsly_cli:main',
        ],
    },
    description='SMSLY Hosting CLI — manage services from the terminal.',
    author='SMSLY Cloud',
    python_requires='>=3.8',
)
