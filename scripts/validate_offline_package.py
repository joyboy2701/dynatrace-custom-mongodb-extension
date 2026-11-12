#!/usr/bin/env python3
"""Run with CPython 3.14; validate dependencies from the final signed ZIP offline."""
import argparse
import io
import os
from pathlib import Path
import platform
import subprocess
import sys
import tempfile
import venv
from zipfile import ZipFile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('package', type=Path)
    args = parser.parse_args()
    if sys.implementation.name != 'cpython' or sys.version_info[:2] != (3, 14):
        parser.error('Run this script with CPython 3.14, e.g. python3.14 scripts/validate_offline_package.py PACKAGE')
    print(f'Runtime: {platform.python_version()}, {platform.system()}, {platform.machine()}', flush=True)
    with tempfile.TemporaryDirectory(prefix='mongodb-offline-') as temporary:
        root = Path(temporary)
        # The signed distributable wraps extension.zip and its signature.
        with ZipFile(args.package) as signed:
            if 'extension.zip.sig' not in signed.namelist():
                raise ValueError('Expected the signed distributable with extension.zip.sig')
            with ZipFile(io.BytesIO(signed.read('extension.zip'))) as extension:
                lib = root / 'lib'
                lib.mkdir()
                for name in extension.namelist():
                    if name.startswith('lib/') and name.endswith('.whl'):
                        (lib / Path(name).name).write_bytes(extension.read(name))
        environment = root / 'venv'
        venv.EnvBuilder(with_pip=True).create(environment)
        python = environment / 'bin/python'
        env = dict(os.environ, PIP_CONFIG_FILE=os.devnull, PIP_DISABLE_PIP_VERSION_CHECK='1')
        command = [str(python), '-m', 'pip', 'install', '--no-index', '--no-cache-dir',
                   '--find-links', str(lib)]
        subprocess.run(command + ['mongodb_custom_query==0.1.39'], check=True, env=env)
        subprocess.run([str(python), '-m', 'pip', 'check'], check=True, env=env)
        subprocess.run([str(python), '-I', '-c',
                        'from importlib.metadata import version; '
                        'import pymongo, pymongo._cmessage, bson._cbson; '
                        'from mongodb_custom_query.__main__ import MongoDBCustomQueryExtension; '
                        'assert version("mongodb_custom_query") == "0.1.39"; '
                        'print("Extension and native PyMongo imports OK:", pymongo.version)'],
                       cwd=root, check=True, env=env)
        # Resolve each bundled target using pip's compatibility checking, fully offline.
        for target in ('3.10', '3.14'):
            for arch in ('x86_64', 'aarch64'):
                subprocess.run(command + ['--dry-run', '--ignore-installed', '--only-binary=:all:',
                                          '--python-version', target,
                                          '--platform', f'manylinux2014_{arch}',
                                          'mongodb_custom_query==0.1.39'], check=True, env=env)
    print('Offline installation, imports, and four-target dependency resolution passed.')


if __name__ == '__main__':
    main()
