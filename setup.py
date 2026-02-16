#!/usr/bin/env python3

from setuptools import setup
from setuptools import find_packages


with open("README.md", "r", encoding="utf-8") as fp:
    long_description = fp.read()


setup(
    name                          = "liteusb",
    version                       = "2025.12",
    description                   = "Small footprint and configurable USB core for LiteX.",
    long_description              = long_description,
    long_description_content_type = "text/markdown",
    author                        = "Hans Baier",
    author_email                  = "foss@hans-baier.de",
    url                           = "https://github.com/hansfbaier/",
    download_url                  = "https://github.com/hansfbaier/liteusb",
    test_suite                    = "test",
    license                       = "BSD",
    python_requires               = "~=3.7",
    install_requires              = [
        "migen",
        "litex",
        "usb-protocol",
    ],
    packages                       = find_packages(),
    package_data                   = {"": ["*.rst", "*.md"]},
    include_package_data           = True,
    platforms                      = ["Any"],
    keywords                       = "HDL ASIC FPGA hardware design USB",
    classifiers                    = [
        "Topic :: Scientific/Engineering :: Electronic Design Automation (EDA)",
        "Environment :: Console",
        "Development Status :: 3 - Alpha",
        "Intended Audience :: Developers",
        "License :: OSI Approved :: BSD License",
        "Operating System :: OS Independent",
        "Programming Language :: Python",
    ],
)
