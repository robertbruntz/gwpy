# -*- coding: utf-8 -*-
# Copyright (C) Duncan Macleod (2017)
#
# This file is part of GWpy.
#
# GWpy is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# GWpy is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with GWpy.  If not, see <http://www.gnu.org/licenses/>.

"""Packaging utilities for the GWpy package
"""

from __future__ import print_function

import datetime
import glob
import hashlib
import os
import subprocess
import sys
import tempfile
from distutils.cmd import Command
from distutils.command.clean import (clean as _clean, log, remove_tree)
from distutils.command.bdist_rpm import bdist_rpm as distutils_bdist_rpm
from distutils.errors import DistutilsArgError

from setuptools.command.bdist_rpm import bdist_rpm
from setuptools.command.egg_info import egg_info as _egg_info

import versioneer

CMDCLASS = versioneer.get_cmdclass()
SETUP_REQUIRES = [
    ({'pytest', 'test', 'prt'}, ['pytest_runner']),
]


# -- custom commands ----------------------------------------------------------

class changelog(Command):
    """Generate the changelog for this project
    """
    description = 'write the changelog for this project'
    user_options = [
        ('format=', 'f', 'output changelog format'),
        ('start-tag=', 's', 'git tag to start with'),
        ('output=', 'o', 'output file path'),
    ]
    FORMATS = ('rpm', 'deb')

    def initialize_options(self):
        self.format = 'rpm'
        self.start_tag = None
        self.output = 'changelog.txt'

    def finalize_options(self):
        if self.format not in self.FORMATS:
            raise DistutilsArgError(
                "--format should be one of {!r}".format(self.FORMATS))
        if self.start_tag:
            self.start_tag = self.start_tag.lstrip('v')

    def format_changelog_entry(self, tag):
        log.debug('    parsing changelog entry for {}'.format(tag))
        if self.format == 'rpm':
            return self._format_entry_rpm(tag)
        elif self.format == 'deb':
            return self._format_entry_deb(tag)
        raise RuntimeError("unsupported changelog format")

    def _format_entry_rpm(self, tag):
        tago = tag.tag
        date = datetime.date.fromtimestamp(tago.tagged_date)
        dstr = date.strftime('%a %b %d %Y')
        tagger = tago.tagger
        message = tago.message.split('\n')[0]
        return "* {} {} <{}>\n- {}".format(dstr, tagger.name, tagger.email,
                                           message)

    def _format_entry_deb(self, tag):
        tago = tag.tag
        date = datetime.datetime.fromtimestamp(tago.tagged_date)
        dstr = date.strftime('%a, %d %b %Y %H:%M:%S')
        tz = int(-tago.tagger_tz_offset / 3600. * 100)
        version = tag.name.strip('v')
        tagger = tago.tagger
        message = tago.message.split('\n')[0]
        return ("gwpy ({}-1) unstable; urgency=low\n\n"
                "  * {}\n\n"
                " -- {} <{}>  {} {:+05d}\n".format(
                    version, message, tagger.name, tagger.email, dstr, tz))

    def get_git_tags(self):
        import git
        repo = git.Repo()
        tags = repo.tags
        tags.reverse()
        log.debug('found {} git tags'.format(len(tags)))
        return tags

    def run(self):
        log.info('creating changelog')
        lines = []
        for tag in self.get_git_tags():
            if self.start_tag and tag.name.lstrip('v') < self.start_tag:
                log.debug('reached start tag ({}), stopping'.format(
                    self.start_tag))
                break
            lines.append(self.format_changelog_entry(tag))
        log.info('writing changelog to {}'.format(self.output))
        with open(self.output, 'w') as f:
            for line in lines:
                print(line, file=f)

CMDCLASS['changelog'] = changelog
SETUP_REQUIRES.append(('changelog', ['GitPython']))


class bdist_spec(bdist_rpm):
    """Generate the RPM spec file for this distribution
    """
    description = 'write the spec file for an RPM distribution'

    def initialize_options(self):
        bdist_rpm.initialize_options(self)
        self.spec_only = True

    def finalize_options(self):
        if self.dist_dir is None:  # write spec into current directory
            self.dist_dir = os.path.curdir
        bdist_rpm.finalize_options(self)

    def run(self):
        # generate changelog
        if self.changelog is None:
            changelogcmd = self.distribution.get_command_obj('changelog')
            with tempfile.NamedTemporaryFile(delete=True, mode='w+') as f:
                self.distribution._set_command_options(changelogcmd, {
                    'format': ('bdist_spec', 'rpm'),
                    'output': ('bdist_spec', f.name),
                })
                self.run_command('changelog')
                f.seek(0)
                self.changelog = self._format_changelog(f.read())

        # execute distutils version of bdist_rpm.run to avoid calling egg_info
        distutils_bdist_rpm.run(self)

CMDCLASS['bdist_spec'] = bdist_spec

orig_egg_info = CMDCLASS.pop('egg_info', _egg_info)


class egg_info(orig_egg_info):
    """Extension to egg_info to build spec file and debian/changelog
    """
    def run(self):
        # generate spec file
        self.run_command('bdist_spec')

        # generate debian/changelog
        self.distribution.have_run.pop('changelog')
        changelogcmd = self.distribution.get_command_obj('changelog')
        self.distribution._set_command_options(changelogcmd, {
            'format': ('egg_info', 'deb'),
            'output': ('egg_info', os.path.join('debian', 'changelog'))})
        self.run_command('changelog')

        orig_egg_info.run(self)

CMDCLASS['egg_info'] = egg_info


class clean(CMDCLASS.pop('clean', _clean)):
    """Custom clean command to remove more temporary files and directories
    """
    def run(self):
        if self.all:
            # remove dist
            if os.path.exists('dist'):
                remove_tree('dist')
            else:
                log.warn("'dist' does not exist -- can't clean it")
            # remove docs
            sphinx_dir = os.path.join(self.build_base, 'sphinx')
            if os.path.exists(sphinx_dir):
                remove_tree(sphinx_dir, dry_run=self.dry_run)
            else:
                log.warn("%r does not exist -- can't clean it", sphinx_dir)
            # remove setup eggs
            for egg in glob.glob('*.egg') + glob.glob('*.egg-info'):
                if os.path.isdir(egg):
                    remove_tree(egg, dry_run=self.dry_run)
                else:
                    log.info('removing %r' % egg)
                    os.unlink(egg)
            # remove Portfile
            portfile = 'Portfile'
            if os.path.exists(portfile) and not self.dry_run:
                log.info('removing %r' % portfile)
                os.unlink(portfile)
        clean.run(self)

CMDCLASS['clean'] = clean


class port(Command):
    """Generate a Macports Portfile for this project from the current build
    """
    description = 'generate a Macports Portfile'
    user_options = [
        ('version=', None, 'the X.Y.Z package version'),
        ('portfile=', None, 'target output file, default: \'Portfile\''),
        ('template=', None,
         'Portfile template, default: \'Portfile.template\''),
    ]

    def initialize_options(self):
        self.version = None
        self.portfile = 'Portfile'
        self.template = 'Portfile.template'
        self._template = None

    def finalize_options(self):
        from jinja2 import Template
        with open(self.template, 'r') as t:
            # pylint: disable=attribute-defined-outside-init
            self._template = Template(t.read())

    def run(self):
        # find dist file
        dist = os.path.join(
            'dist',
            '%s-%s.tar.gz' % (self.distribution.get_name(),
                              self.distribution.get_version()))
        # run sdist if needed
        if not os.path.isfile(dist):
            self.run_command('sdist')
        # get checksum digests
        log.info('reading distribution tarball %r' % dist)
        with open(dist, 'rb') as fobj:
            data = fobj.read()
        log.info('recovered digests:')
        digest = dict()
        digest['rmd160'] = self._get_rmd160(dist)
        for algo in [1, 256]:
            digest['sha%d' % algo] = self._get_sha(data, algo)
        for key, val in digest.iteritems():
            log.info('    %s: %s' % (key, val))
        # write finished portfile to file
        with open(self.portfile, 'w') as fport:
            fport.write(self._template.render(
                version=self.distribution.get_version(), **digest))
        log.info('portfile written to %r' % self.portfile)

    @staticmethod
    def _get_sha(data, algorithm=256):
        hash_ = getattr(hashlib, 'sha%d' % algorithm)
        return hash_(data).hexdigest()

    @staticmethod
    def _get_rmd160(filename):
        out = subprocess.check_output(['openssl', 'rmd160', filename])
        return out.splitlines()[0].rsplit(' ', 1)[-1]

CMDCLASS['port'] = port
SETUP_REQUIRES.append(('port', ['jinja2']))


# -- utility functions --------------------------------------------------------

def get_setup_requires():
    """Return the list of packages required for this setup.py run
    """
    # don't force requirements if just asking for help
    if {'--help', '--help-commands'}.intersection(sys.argv):
        return list()

    # otherwise collect all requirements for all known commands
    reqlist = []
    for key, dependencies in SETUP_REQUIRES:
        try:
            requires = key.intersection(sys.argv)
        except AttributeError:
            requires = key in sys.argv
        if requires:
            reqlist.extend(dependencies)

    return reqlist


def get_scripts(scripts_dir='bin'):
    """Get relative file paths for all files under the ``scripts_dir``
    """
    scripts = []
    for (dirname, _, filenames) in os.walk(scripts_dir):
        scripts.extend([os.path.join(dirname, fn) for fn in filenames])
    return scripts
