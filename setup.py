'''A setuptools based setup module.

https://packaging.python.org/en/latest/distributing.html '''
from setuptools import setup, find_packages
from setuptools.command.install import install
from setuptools.command.develop import develop
from setuptools.extension import Extension
from codecs import open
from os import path
import os
import time

def auto_setup_db():
    import django
    os.environ['DJANGO_SETTINGS_MODULE'] = 'balsam.django_config.settings'
    django.setup()

class PostInstallCommand(install):
    '''Post-installation for installation mode'''
    def run(self):
        auto_setup_db()
        install.run(self)
        from Cython.Build import cythonize
        extensions = [
            Extension("balsam.service._packer", 
                      ["balsam/service/_packer.pyx"])
        ]
        setup(ext_modules=cythonize(extensions))


class PostDevelopCommand(develop):
    '''Post-installation for installation mode'''
    def run(self):
        auto_setup_db()
        develop.run(self)
        from Cython.Build import cythonize
        extensions = [
            Extension("balsam.service._packer", 
                      ["balsam/service/_packer.pyx"])
        ]
        setup(ext_modules=cythonize(extensions))


here = path.abspath(path.dirname(__file__))
activate_script = path.join(here, 'balsam', 'scripts', 'balsamactivate')
deactivate_script = path.join(here, 'balsam', 'scripts', 'balsamdeactivate')

with open(path.join(here, 'README.md'), encoding='utf-8') as f:
    long_description = f.read()

setup(
    name='balsam',
    version='0.1',
    description='HPC Edge service and workflow management',
    long_description=long_description,
    
    url='', # Home page
    author='',
    author_email='',

    classifiers = [],

    keywords='',

    packages=find_packages(exclude=['docs','__pycache__','data','experiments','log',]),

    install_requires=['cython', 'django', 'jinja2', 'psycopg2-binary'],

    include_package_data=True,

    # Command-line bash scripts (to be used as "source balsamactivate")
    scripts = [activate_script, deactivate_script],

    # Register command-line tools here
    entry_points={
        'console_scripts': [
            'balsam = balsam.scripts.cli:main',
            'balsam-test = run_tests:main'
        ],
        'gui_scripts': [],
    },

    # Balsam DB auto-setup post installation
    cmdclass={
        'develop': PostDevelopCommand,
        'install': PostInstallCommand,
    },
)
