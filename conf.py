# Configuration file for the Sphinx documentation builder.
#
# For the full list of built-in configuration values, see the documentation:
# https://www.sphinx-doc.org/en/master/usage/configuration.html
import os
import sys
from datetime import datetime
sys.path.insert(0, os.path.abspath('.'))
sys.path.insert(1, os.path.abspath('..'))
sys.path.insert(2, os.path.abspath(os.path.join("..", "esat")))
sys.path.insert(3, os.path.abspath(os.path.join("..", "eval")))


# -- Project information -----------------------------------------------------
# https://www.sphinx-doc.org/en/master/usage/configuration.html#project-information

project = 'Environmental Source Apportionment Toolkit (ESAT)'
copyright = '2024, EPA'
author = 'Deron Smith'
release = datetime.now().strftime("%m/%d/%Y")

# -- General configuration ---------------------------------------------------
# https://www.sphinx-doc.org/en/master/usage/configuration.html#general-configuration

extensions = ['myst_parser', 'sphinx.ext.autosummary', 'sphinx.ext.autodoc', 'sphinx.ext.todo', 'sphinx.ext.napoleon',
              'sphinx_click']

autodoc_typehints = "signature"
autodoc_default_options = {
    'members': True,
    'undoc-members': True,
    'memer-order': 'bysource'
}

templates_path = ['_templates']
exclude_patterns = ['_build', 'Thumbs.db', '.DS_Store']


# -- Options for HTML output -------------------------------------------------
# https://www.sphinx-doc.org/en/master/usage/configuration.html#options-for-html-output

html_theme = 'sphinx_rtd_theme'
html_static_path = ['docs/static']
html_sidebars = {
    '*': [
        'searchbox.html',
        'relations.html',
        'globaltoc.html'
    ]
}