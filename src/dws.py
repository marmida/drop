#!/usr/bin/env python
#
# Copyright (c) 2009, Sebastien Mirolo
#   All rights reserved.
#
#   Redistribution and use in source and binary forms, with or without
#   modification, are permitted provided that the following conditions are met:
#     * Redistributions of source code must retain the above copyright
#       notice, this list of conditions and the following disclaimer.
#     * Redistributions in binary form must reproduce the above copyright
#       notice, this list of conditions and the following disclaimer in the
#       documentation and/or other materials provided with the distribution.
#     * Neither the name of fortylines nor the
#       names of its contributors may be used to endorse or promote products
#       derived from this software without specific prior written permission.

#   THIS SOFTWARE IS PROVIDED BY Sebastien Mirolo ''AS IS'' AND ANY
#   EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED
#   WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
#   DISCLAIMED. IN NO EVENT SHALL Sebastien Mirolo BE LIABLE FOR ANY
#   DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES
#   (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES;
#   LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND
#   ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT
#  (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THIS
#   SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.

# This script implements workspace management.
#
# The workspace manager script is used to setup a local machine
# with third-party prerequisites and source code under revision
# control such that it is possible to execute a development cycle
# (edit/build/run) on a local machine.
#
# example: 
# dws --exclude 'contrib' build ~/workspace/fortylines ~/build/fortylines/install
#
# http://www.kernel.org/pub/software/scm/git/docs/everyday.html
#
# http://www.gelato.unsw.edu.au/archives/git/0511/11390.html
# If the only thing you would want to do is to build it, then you
# could 'git-tar-tree v2.6.14' and extract that on your notebook.
# The output is just a tar so there will no history, though.


__version__ = '0.1'

import hashlib
import re, os, optparse, shutil
import socket, subprocess, sys, tempfile
import xml.dom.minidom, xml.sax

log = None
useDefaultAnswer = False
excludePats = []

class Error(Exception):
    '''This type of exception is used to identify "expected" 
    error condition and will lead to a useful message. 
    Other exceptions are not caught when *__main__* executes,
    and an internal stack trace will be displayed. Exceptions
    which are not *Error*s are concidered bugs in the workspace 
    management script.'''
    def __init__(self,msg="error",code=1):
        self.code = code
        self.msg = msg

    def __str__(self):
        return 'error ' + str(self.code) + ': ' + self.msg + '\n'

class CircleError(Error):
    '''Thrown when a circle has been detected while doing
    a topological traversal of a graph.'''
    def __init__(self,source,target):
        Error.__init__(self,msg="circle exception while traversing edge from " \
                           + source + " to " + target)


class Context:
    '''The workspace configuration file contains environment variables used
    to update, build and package projects. The environment variables are roots
    of the dependency graph as most other routines depend at the least 
    on srcTop and buildTop.'''

    configName = 'ws.mk'

    def __init__(self):
        # \todo default used to be os.path.dirname(os.path.dirname(os.getcwd()))
        self.cacheTop = Pathname('cacheTop',
                          'Root of the tree where cached packages are fetched',
                          default=os.getcwd())
        self.remoteCacheTop = Pathname('remoteCacheTop',
             'Root of the remote tree where packages are located',
                  default='fortylines.com:/var/fortylines')
        self.environ = { 'buildTop': Pathname('buildTop',
             'Root of the tree where intermediate files are created.',
                                              self.cacheTop,default='build'), 
                         'srcTop' : Pathname('srcTop',
             'Root of the tree where the source code under revision control lives on the local machine.',self.cacheTop,default='reps'),
                         'binDir': Pathname('binDir',
             'Root of the tree where executables are installed',
                                            self.cacheTop),
                         'includeDir': Pathname('includeDir',
             'Root of the tree where include files are installed',
                                                self.cacheTop),
                         'libDir': Pathname('libDir',
             'Root of the tree where libraries are installed',
                                            self.cacheTop),
                         'etcDir': Pathname('etcDir',
             'Root of the tree where extra files are installed',
                                            self.cacheTop,'etc/dws'),
                         'shareDir': Pathname('shareDir',
             'Root of the tree where shared files are installed',
                                            self.cacheTop),
                         'logDir': Pathname('logDir',
             'Root of the tree where logs are installed',
                                            self.cacheTop,'log'),
                         'cacheTop': self.cacheTop,
                         'remoteCacheTop': self.remoteCacheTop,
                         'remoteSrcTop': Pathname('remoteSrcTop',
             'Root the remote tree where repositories are located',
                                          self.remoteCacheTop,'reps'),
                        'darwinTargetVolume': SingleChoice('darwinTargetVolume',
              'Destination of installed packages on a Darwin local machine. Installing on the "LocalSystem" requires administrator privileges.',
              choices=[ ['LocalSystem', 
                         'install packages on the system root for all users'],
                        ['CurrentUserHomeDirectory', 
                         'install packages for the current user only'] ]),
              # These pathnames are not used for building code. They are used
              # by the integrity (i.e project "machines") scripts to backup
              # and replicate important files.
              'backupDir': Pathname('backupDir',
                   'Directory where archive (.tar.bz2) files are stored',
                                    self.cacheTop,'backup'),
              'replicatePath': Pathname('replicatePath',
                   'Path, usually on a remote machine, where replicated copies are stored. This path might, probably should, point to another machine than *remoteCacheTop*',
                                    self.remoteCacheTop,'replicate')  }

        self.buildTopRelativeCwd = None
        try:
            self.locate()
            # -- Read the environment variables set in the config file.
            configFile = open(self.configFilename)
            line = configFile.readline()
            while line != '':
                look = re.match('(\S+)\s*=\s*(\S+)',line)
                if look != None:
                    if not look.group(1) in self.environ:
                        self.environ[look.group(1)] = Pathname(look.group(1),
                                                               'no description')
                    self.environ[look.group(1)].value = look.group(2)
                line = configFile.readline()
            configFile.close()        
        except IOError:
            None
        except:
            raise


    def cachePath(self,name):
        '''Absolute path to a file in the cached packages 
        directory hierarchy.'''
        return os.path.join(self.value('cacheTop'),name)


    def remoteCachePath(self,name):
        '''Absolute path to access a cached file on the remote machine.''' 
        return os.path.join(self.value('remoteCacheTop'),name)

    def remoteSrcPath(self,name):
        '''Absolute path to access a repository file on the remote machine.''' 
        return os.path.join(self.value('remoteSrcTop'),name)        

    def cwdProject(self):
        '''Returns a project name based on the current directory.'''
        if not self.buildTopRelativeCwd:
            self.environ['buildTop'].default = os.path.dirname(os.getcwd())
            log.write('no workspace configuration file could be ' \
               + 'found from ' + os.getcwd() \
               + ' all the way up to /. A new one, called ' + self.configName\
               + ', will be created in *buildTop* after that path is set.\n')
            self.configFilename = os.path.join(self.value('buildTop'),
                                               self.configName)
            self.save()
            self.locate()
        return self.buildTopRelativeCwd

    def isControlled(self,name):
        '''Returns True if the source directory associated with project *name*
           contains information about a revision control system (i.e. CVS/, 
           .git/, etc.).'''
        if os.path.exists(os.path.join(context.srcDir(name),'.git')):
            return True
        if os.path.exists(os.path.join(context.srcDir(name),'CVS')):
            return True
        return False

    def dbPathname(self,remote=False):
        '''Absolute pathname to the project index file.'''
        if remote:
            return self.remoteCachePath('db.xml')
        return os.path.join(self.value('etcDir'),'db.xml')

    def host(self):
        '''Returns the distribution on which the script is running.'''
        dist = None
        # \todo This code was working on python 2.5 but not in 2.6
        #   hostname = socket.gethostbyaddr(socket.gethostname())
        #   hostname = hostname[0]
        # replaced by the following line
        hostname = socket.gethostname()
        sysname, nodename, release, version, machine = os.uname()
        if sysname == 'Darwin':
            dist = 'Darwin'
        elif sysname == 'Linux':
            version = open('/proc/version')
            line = version.readline()
            while line != '':
                for d in [ 'Ubuntu', 'fedora' ]:
                    look = re.match('.*' + d + '.*',line)
                    if look:
                        dist = d
                        break
                if dist:
                    break
                line = version.readline()
            version.close()
            if dist:
                dist = dist.capitalize()
        return dist

    def linkPath(self,paths,installName):
        '''Link a set of files in paths into the installName directory.'''
        for path in paths:
            install = None
            if installName == 'libDir':
                install = linkPathLib(path)
            elif installName == 'includeDir':
                # install = linkPathInclude(path)
                dirname, header = os.path.split(path)
                if dirname != 'include':
                    install = os.path.join(self.value(installName),
                                           os.path.basename(dirname))
                    path = os.path.dirname(path)
            if not install:
                install = os.path.join(self.value(installName),
                                       os.path.basename(path))
            if not os.path.exists(os.path.dirname(install)):
                os.makedirs(os.path.dirname(install))
            # In the following two 'if' statements, we are very careful
            # to only remove/update symlinks and leave other files 
            # present in [bin|lib|...]Dir 'as is'.
            if os.path.islink(install):
                os.remove(install)
            if not os.path.exists(install) and os.path.exists(path):
                os.symlink(path,install)

    def locate(self):
        '''Locate the workspace configuration file and derive the project
        name out of its location.'''
        try:
            self.buildTopRelativeCwd, self.configFilename \
                = searchBackToRoot(self.configName)
        except IOError, e:
            configFromBuildTop = os.path.join(self.environ['buildTop'].default,
                                              self.configName)
            if not os.path.isfile(configFromBuildTop):
                raise e
            self.configFilename = configFromBuildTop
            self.buildTopRelativeCwd = None
        if self.buildTopRelativeCwd == '.':
            self.buildTopRelativeCwd = os.path.basename(os.getcwd())
            look = re.match('([^-]+)-.*',self.buildTopRelativeCwd)
            if look:
                # Change of project name in index.xml on "make dist-src".
                # self.buildTopRelativeCwd = look.group(1)
                None

    def logname(self):
        filename = os.path.join(self.value('etcDir'),'dws.log')
        if not os.path.exists(os.path.dirname(filename)):
            os.makedirs(os.path.dirname(filename))
        return filename

    def objDir(self,name):
        return os.path.join(self.value('buildTop'),name)

    def save(self):
        '''Write the config back to a file.'''
        try:
            configFile = open(self.configFilename,'w')
        except:
            self.configFilename = self.objDir(self.configName)
            configFile = open(self.configFilename,'w')
        keys = sorted(self.environ.keys())
        configFile.write('# configuration for development workspace\n\n')
        for key in keys:
            if self.environ[key].value:
                configFile.write(key + '=' + self.environ[key].value + '\n')
        configFile.close()

    def srcDir(self,name):
        return os.path.join(self.value('srcTop'),name)

    def value(self,name):
        '''returns the value of the workspace variable *name*. If the variable
        has no value yet, a prompt is displayed for it.'''
        if not name in self.environ:
            raise Error("Trying to read unknown variable " + name + ".\n")
        if not self.environ[name].value:
            if selectVariable(self.environ[name]):
                self.save()
        return self.environ[name].value

# Formats help for script commands. The necessity for this class 
# can be understood by the following posts on the internet:
# - http://groups.google.com/group/comp.lang.python/browse_thread/thread/6df6e6b541a15bc2/09f28e26af0699b1
# - http://www.alexonlinux.com/pythons-optparse-for-human-beings
#
# \todo The argparse (http://code.google.com/p/argparse/) might be part
#       of the standard python library and address the issue at some point.
class CommandsFormatter(optparse.IndentedHelpFormatter):
  def format_epilog(self, description):
    import textwrap
    result = "\nCommands:\n"
    if description: 
        desc_width = self.width - self.current_indent
        indent = " "*self.current_indent
        bits = description.split('\n')
        formatted_bits = [
          textwrap.fill(bit,
            desc_width,
            initial_indent=indent,
            subsequent_indent=indent)
          for bit in bits]
        result = result + "\n".join(formatted_bits) + "\n"
    return result         


class IndexProjects:
    '''Index file containing the graph dependency for all projects.'''

    def __init__(self, context, filename = None):
        self.context = context
        self.parser = xmlDbParser(context)
        self.filename = filename

 
    def closure(self, dgen):
        '''Find out all dependencies from a root set of projects as defined 
        by the dependency generator *dgen*.'''
        while len(dgen.vertices) > 0:
            self.parse(dgen)
            dgen.nextLevel()
        return dgen.topological()
        

    def parse(self, dgen):
        '''Parse the project index and generates callbacks to *dgen*'''
        self.validate()
        self.parser.parse(self.filename,dgen)


    def validate(self,force=False):
        '''Create the project index file if it does not exist
        either by fetching it from a remote server or collecting
        projects indices locally.'''
        if not self.filename:
            self.filename = self.context.dbPathname()
        if self.filename == self.context.dbPathname():
            if not os.path.exists(self.filename) and not force:
                # index or copy.
                selection = selectOne('The project index file could not '
                                      + 'be found at ' + self.filename \
                                          + '. It can be regenerated through one ' \
                                          + 'of the two following method:',
                                      [ [ 'fetching', 'from remote server' ],
                                        [ 'indexing', 
                                          'local projects in the workspace' ] ])
                if selection == 'fetching':
                    force = True
                if selection == 'indexing':
                    pubCollect([])
            if force:
                fetch({os.path.join(self.context.host(),
                                    os.path.basename(self.filename)):None},
                      context.value('etcDir'),force)
        elif not os.path.exists(self.filename):
            raise Error(self.filename + ' does not exist.')


class LogFile:
    
    def __init__(self,logfilename):
        self.logfile = open(logfilename,'w')
        self.logfile.write('<?xml version="1.0" ?>\n')
        self.logfile.write('<book>\n')

    def close(self):
        self.logfile.write('</book>\n')
        self.logfile.close()        

    def error(self,text):
        sys.stderr.write(text)
        self.logfile.write(text)

    def footer(self,status):
        self.logfile.write(']]>\n<status>' + status + '</status>\n')
        self.logfile.write('</section>\n')

    def header(self, text):
        sys.stdout.write('make ' + text + '...\n')
        self.logfile.write('<section id="' + text + '">\n')
        self.logfile.write('<![CDATA[')

    def flush(self):
        sys.stdout.flush()
        self.logfile.flush()

    def write(self, text):
        sys.stdout.write(text)
        self.logfile.write(text)

class PdbHandler:
    '''Callback interface for a project index as generated by a PdbParser.
       The generic handler does not do anything. It is the responsability of
       implementing classes to filter callback events they care about.'''
    def __init__(self):
        None

    def startProject(self, name):
        None

    def dependency(self, name, deps, excludes=[]):
        None

    def description(self, text):
        None

    def endParse(self):
        None

    def endProject(self):
        None

    def control(self, type, url):
        None

    def package(self, filename, sha1):
        None

    def sources(self, name, patched):
        None

    def version(self, text):
        '''This can only be added from package'''
        None


class Unserializer(PdbHandler):
    '''Aggregate dependencies for a set of projects only when prerequisites
    can not be found on the system.'''

    def __init__(self, builds=None):
        PdbHandler.__init__(self)
        self.project = None
        self.projects = {}
        self.builds = builds

    def asProject(self, name):
        return self.projects[name]

    def control(self, type, url):
        if self.project:
            self.projects[self.project].control = Control(type,url)

    def dependency(self, name, deps, excludes=[]):
        if self.project:
            self.projects[self.project].buildDeps[name] = deps
            self.projects[self.project].buildExcludes[name] = excludes

    def description(self, text):
        if self.project:
            self.projects[self.project].description = text

    def package(self, filename, sha1):
        if self.project:
            self.projects[self.project].package = Package(filename,sha1)        

    def startProject(self, name):
        self.project = None
        if (not self.builds) or (name in self.builds):
            self.project = name
            if not name in self.projects:
                self.projects[name] = Project(name)


class DependencyGenerator(Unserializer):
    '''Aggregate dependencies for a set of projects'''

    def __init__(self, projects, excludePats = []):
        '''*projects* is a list of root projects used to generate
        the dependency graph. *excludePats* is a list of projects which
        should be removed from the final topological order.'''
        Unserializer.__init__(self)
        # None if we don't record dependencies for a project and the name 
        # of the project otherwise.
        self.source = None
        # This contains a list of list of edges. When levels is traversed last 
        # to first and each edge's source vertex is printed, it displays 
        # a topological ordering of the selected projects.
        # In other words, levels holds each recursing of a breadth-first search
        # algorithm through the graph of projects from the roots.
        # We store edges in each level rather than simply the source vertex 
        # such that we can detect cycles. That is when an edge would be 
        # traversed again.
        roots = []
        self.excludePats = excludePats
        for p in projects:         
            roots += [ [ None, p ] ]
        self.levels = [ roots ]
        self.vertices = projects
        # *cuts* is a list of Project(). Each instance contains resolution
        # for links on the local machine.
        self.cuts = []
        # *missings* contains a list of dependencies which cannot be fullfiled
        # with the current configuration of the local machine.
        # *prerequisites* contains a list of missing projects, which is to say
        # projects which are targets of *missings* dependencies.
        self.missings = []
        self.prerequisites = []
        self.prereqDeps = {}
        self.prereqExcludes = {}
        self.nextLevel()

    def candidates(self, filtered=[]):
        '''Returns a list of rows where each row contains expanded information
        for each missing project. Only projects which do not appear in filtered
        will be part of the returned list.'''
        controls = []
        packages = []
        for name in self.prerequisites:
            if not name in filtered:
                # If the prerequisites is not defined as an explicit
                # package, we will assume the prerequisite name is
                # enough to install the required tools for the prerequisite.
                row = [ name ]
                if name in self.projects:
                    if self.projects[name].installedVersion:
                        row += [ self.projects[name].installedVersion ]
                    if self.projects[name].control:
                        controls += [ row ]
                    elif self.projects[name].package:
                        packages += [ row ]
                    else:
                        packages += [ row ]
                else:
                    packages += [ row ]
        return controls, packages
 
    def dependency(self, name, deps, excludes=[]):
        if self.source:
            self.projects[self.source].buildDeps[name] = deps
            self.projects[self.source].buildExcludes[name] = excludes
            if self.addDep(name,deps,excludes):
                self.levels[0] += [ [ self.source, name ] ]
            else:
                # The parsing pass will gather all unique prerequisites.
                # Prerequisites will be searched on the local system
                # after parsing is completed.
                if name in self.prereqDeps:
                    for key in deps:
                        if key in self.prereqDeps[name]:
                           for value in deps[key]:
                               if not value in self.prereqDeps[name][key]:
                                   self.prereqDeps[name][key] += [ value ]
                        else:
                            self.prereqDeps[name][key] = deps[key]
                    for exclude in excludes:
                        if not exclude in self.prereqExcludes[name]:
                            self.prereqExcludes[name] += [ exclude ]
                else:
                    self.prereqDeps[name] = deps
                    self.prereqExcludes[name] = excludes

    def endParse(self):
        # Search for prerequisites on the local system
        buildDeps = {}
        for name in self.prereqDeps:
            buildDeps[name], complete = findPrerequisites(self.prereqDeps[name],
                                                    self.prereqExcludes[name])
            if complete:
                self.cuts += [ name ]
            else:
                if not name in self.prerequisites:
                    self.prerequisites += [ name ]
        # Update project dependencies that can be satisfied
        for source in self.projects:
            for name in self.projects[source].buildDeps:
                if name in self.cuts:
                    # In case the superset of dependencies for all projects 
                    # that depend on a prerequisite is satisfied, we safely 
                    # extend the subset of dependencies of such project.
                    # This avoids complexity generating accurate intermediate 
                    # results when the end result will be identical. 
                    self.projects[source].buildDeps[name] = buildDeps[name]
                else:
                    if not [ source, name ] in self.missings:
                        self.missings += [ [ source, name ] ]

    def nextLevel(self, filtered=[]):
        '''Going one step further in the breadth-first recursion introduces 
        a new level. All missing edges whose target is in *filtered* will 
        be added to the dependency graph.
        By definition, all missing projects which are not in *filtered* 
        will be added as cut points. From this time, *cuts* contains 
        *complete*d projects as well as projects that still need to be 
        resolved before links are created.'''
        for newEdge in self.missings:
            if newEdge[1] in filtered:
                self.levels[0] += [ newEdge ]
        self.missings = []
        for package in self.prerequisites:
            if not package in filtered:
                self.cuts += [ package ]
        #self.prereqDeps = {}
        #self.prereqExcludes = {}
        self.prerequisites = []
        self.vertices = []
        for newEdge in self.levels[0]:
            # We first walk the tree of previously recorded edges to find out 
            # if we detected a cycle.
            if len(self.levels) > 1:
                for level in self.levels[1:]:
                    for edge in level:
                        if edge[0] == newEdge[0] and edge[1] == newEdge[1]:
                            raise CircleError(edge[0],edge[1])
            if not newEdge[1] in self.vertices:
                # Insert each vertex once. 
                self.vertices += [ newEdge[1] ]
        # If an edge's source is matching a vertex added 
        # to the next level, obviously, it was too "late"
        # in the topological order.
        newLevel = []
        for edge in self.levels[0]:
            found = False
            for vertex in self.vertices:
                if edge[0] == vertex:
                    found = True
                    break
            if not found:
                newLevel += [ edge ] 
        self.levels[0] = newLevel            
        self.levels.insert(0,[])

    def startProject(self, name):
        Unserializer.startProject(self,name)
        self.source = None
        if name in self.vertices:
            self.source = name

    def addDep(self, name, deps, excludes=[]):
        return True

    def topological(self):
        '''Returns a topological ordering of projects selected.'''
        sorted = []
        for level in self.levels:
            for edge in level:
                if not edge[1] in sorted:
                    sorted += [ edge[1] ] 
        results = []
        for name in sorted:
            found = False
            for excludePat in self.excludePats:
                if re.match(excludePat,name):
                    found = True
                    break
            if not found:
                results += [ name ]
        return results


class MakeGenerator(DependencyGenerator):
    '''As other dependency generators, *MakeGenerator* is initialized
    with a set of projects. All prerequisite projects necessary to **build** 
    that set which have an associated directory in *srcTop* will be added
    to the *found* list.
    For other prerequisiste projects, the script will search for necessary 
    executables, headers, libraries, etc. on the local machine. If they
    all can be found, the prerequisiste project will be added to 
    the *installed* list, else the prerequisiste project will be added to
    the *missing* list.
    It is the responsability of the owner of the MakeGenerator instance
    to check there are no *missing* prerequisites and act appropriately.
    '''
    
    def __init__(self, projects, excludePats = []):
        # \todo add pre-built packages in download list.
        # images = {}
        #    filenames = []
        #    for name in packages:
        #        package = handler.asProject(name).package
        #        images[ os.path.join(context.host(),package.filename) ] \
        #            = package.sha1
        #        filenames += [ package.filename ]
        #    images.update(extraFetches)
        self.extraFetches = {}
        DependencyGenerator.__init__(self,projects,excludePats)

    def addDep(self, name, deps, excludes=[]):
        if os.path.isdir(context.srcDir(name)):
            return True
        return False
    
    def sources(self, name, patched={}):
        if self.source:
            found, version = findCache(patched)
            for source in patched:
                if not context.cachePath(source) in found:
                    self.extraFetches[source] \
                        = os.path.join('srcs',patched[source])


class DerivedSetsGenerator(PdbHandler):
    '''Generate different sets of projects which are of interests 
    to the workspace management algorithms.
    - roots          set of projects which are not dependency 
                     for any other project.
    - repositories   set of projects which are managed under a source 
                     revision control system.
    '''
   
    def __init__(self):
        self.roots = []
        self.nonroots = []
        self.repositories = []
        self.curProjName = None

    def control(self, type, url):
        self.repositories += [ self.curProjName ]

    def dependency(self, name, deps, excludes=[]):
        if name in self.roots:
            self.roots.remove(name)
        if not name in self.nonroots:
            self.nonroots += [ name ]

    def endProject(self):
        self.curProjName = None

    def startProject(self, name):
        self.curProjName = name
        if not name in self.nonroots:
            self.roots += [ name ]


class Variable:
    
    def __init__(self,name,descr=None):
        self.name = name
        self.descr = descr
        self.value = None

class Pathname(Variable):
    
    def __init__(self,name,descr=None,base=None,default=None):
        Variable.__init__(self,name,descr)
        self.base = base
        self.default = default

class SingleChoice(Variable):

    def __init__(self,name,descr=None,choices=[]):
        Variable.__init__(self,name,descr)
        self.choices = choices

class Control:

    def __init__(self, type, url):
        self.type = type
        self.url = url

class Package:

    def __init__(self, filename, sha1):
        self.filename = filename
        self.sha1 = sha1


class Project:
    '''*complete* will be True whenever all necessary executables, headers,
    libraries, etc. have been found on the local machine. At which point.
    *deps* contains such resolution. Otherwise, *deps* contains the 
    required files and *excludes* the excluded versions.'''

    def __init__(self, name):
        self.name = name
        self.buildDeps = {}
        self.buildExcludes = {}
        self.description = None
        self.complete = False
        self.control = None
        self.package = None
        self.patched = []
        self.installedVersion = None

    def __str__(self):
        return 'project ' + self.name + '\n' \
            + '\t' + str(self.description) + '\n' \
            + '\tfound version ' + str(self.installedVersion) \
            + ' installed locally\n' \
            + '\tbuildDeps: ' + str(self.buildDeps) + '\n' \
            + '\tbuildExcludes: ' + str(self.buildExcludes) + '\n' \
            + '\tcomplete: ' + str(self.complete) + '\n' \
            + '\tcontrol: ' + str(self.control) + '\n' \
            + '\tpackage: ' + str(self.package) + '\n' \

class UbuntuIndexWriter(PdbHandler):
    '''As the index file parser generates callback, an instance of this class
    will rewrite the exact same information in a format compatible with apt.'''
    def __init__(self, out):
        self.out = out

    def startProject(self, name):
        self.out.write('Package: ' + name + '\n')

    def dependency(self, name, deps, excludes=[]):
        self.out.write('Depends: ' + ','.join(deps.keys()) + '\n')

    def description(self, text):
        self.out.write('Description:' + text)
    
    def endProject(self):
        self.out.write('\n')

    def control(self, type, url):
        self.out.write('ControlType:' + type + '\n')
        self.out.write('ControlUrl:' + url + '\n')

    def version(self, text):
        self.out.write('Version:' + text)


class xmlDbParser(xml.sax.ContentHandler):
    '''Parse a project database stored as an XML file on disc and generate
       callbacks on a PdbHandler. The handler will update its state
       based on the callback sequence.
       '''

    # Global Constants for the database parser
    tagDb = 'book'
    tagBuild = 'build'
    tagDepend = 'xref'
    tagDescription = 'description'
    tagHash = 'sha1'
    tagInstall = 'install'
    tagPackage = 'package'
    tagProject = 'section'
    tagSrc = 'src'
    tagUrl = 'url'
    tagVersion = 'version'
    tagPattern = '.*<' + tagProject + '\s+id="(.*)"'
    trailerTxt = '</book>'

    def __init__(self, context=None, build=True):
        self.build = build
        self.context = context
        self.handler = None
        self.depName = None
        self.deps = {}
        self.src = None
        self.patchedSourcePackages = {}

    def startElement(self, name, attrs):
        '''Start populating an element.'''
        self.text = ''
        if name == self.tagBuild:
            self.url = None
        elif name == self.tagProject:
            self.patchedSourcePackages = {}
            self.filename = None
            self.sha1 = None
            self.src = None
            self.handler.startProject(attrs['id'])
        elif name == self.tagDepend:
            self.depName = attrs['linkend']
            self.deps = {}
            self.excludes = []
        elif name == self.tagInstall:
            if 'version' in attrs:
                self.handler.install(attrs['mode'],attrs['version'])
            else:
                self.handler.install(attrs['mode'])
        elif name == self.tagPackage:
            self.filename = attrs['name']
        elif name in [ 'bin', 'include', 'lib', 'etc', 'share' ]:
            if not name in self.deps:
                self.deps[name] = []
            self.deps[name] += [ attrs['name'] ]
            if 'excludes' in attrs:
                self.excludes += attrs['excludes'].split(',')
        elif name == self.tagSrc:
            self.src = os.path.join('srcs',attrs['name'])
 
    def characters(self, ch):
        self.text += ch

    def endElement(self, name):
        '''Once the element is fully populated, call back the simplified
           interface on the handler.'''
        if name == self.tagDb:
            self.handler.endParse()
        elif name == self.tagBuild:
            self.handler.control(self.type, self.url)
        elif name == self.tagDepend:
            self.handler.dependency(self.depName, self.deps,self.excludes)
            self.depName = None
        elif name == self.tagDescription:
            self.handler.description(self.text)
        elif name == self.tagProject:
            self.handler.sources(name,self.patchedSourcePackages)
            if self.filename:
                self.handler.package(self.filename,self.sha1)
            self.handler.endProject()
        elif name == self.tagHash:
            if self.src:
                self.patchedSourcePackages[ self.src ] = self.text.strip()
            else:
                self.sha1 = self.text
        elif name == self.tagSrc:
            self.src = None
        elif name == self.tagUrl:
            self.url = self.text
            self.type = self.depName
        elif name == self.tagVersion:
            self.handler.version(self.text)

    def parse(self, pathname, handler):
        '''This is the public interface for one pass through the database
           that generates callbacks on the handler interface.'''
        self.handler = handler
        parser = xml.sax.make_parser()
        parser.setFeature(xml.sax.handler.feature_namespaces, 0)
        parser.setContentHandler(self)
        parser.parse(pathname)

    # The following methods are used to merge multiple databases together.

    def copy(self, dbNext, dbPrev, removeProjectEndTag=False):
        '''Copy lines in the dbPrev file until hitting the definition
        of a package and return the name of the package.'''
        name = None
        line = dbPrev.readline()
        while line != '':
            look = re.match(self.tagPattern,line)
            if look != None:
                name = look.group(1)
                break
            writeLine = True
            look = re.match('.*' + self.trailerTxt,line)
            if look:
                writeLine = False
            if removeProjectEndTag:
                look = re.match('.*</' + self.tagProject + '>',line)
                if look:
                    writeLine = False
            if writeLine:
                dbNext.write(line)
            line = dbPrev.readline()
        return name


    def next(self, dbPrev):
        '''Skip lines in the dbPrev file until hitting the definition
        of a package and return the name of the package.'''
        name = None
        line = dbPrev.readline()
        while line != '':
            look = re.match(self.tagPattern,line)
            if look != None:
                name = look.group(1)
                break
            line = dbPrev.readline()
        return name

    def startProject(self, dbNext, name):
        dbNext.write('  <' + self.tagProject + ' id="' + name + '">\n')
        None

    def trailer(self, dbNext):  
        '''XML files need a finish tag. We make sure to remove it while
           processing Upd and Prev then add it back before closing 
           the final file.'''
        dbNext.write(self.trailerTxt)


def createIndexPathname( dbIndexPathname, dbPathnames ):
    '''create a global dependency database (i.e. project index file) out of
    a set local dependency index files.'''
    parser = xmlDbParser()
    dir = os.path.dirname(dbIndexPathname)
    if not os.path.isdir(dir):
        os.makedirs(dir)
    dbNext = sortBuildConfList(dbPathnames,parser)
    dbIndex = open(dbIndexPathname,'wb')
    dbNext.seek(0)
    shutil.copyfileobj(dbNext,dbIndex)
    dbNext.close()
    dbIndex.close()


def derivedRoots(name):
    '''Derives a list of directory names based on the PATH 
    environment variable.'''
    dirs = []
    for p in os.environ['PATH'].split(':'):
        dir = os.path.join(os.path.dirname(p),name)
        if os.path.isdir(dir):
            dirs += [ dir ]
    return [ context.value(name + 'Dir') ] + dirs


def findBin(names,excludes=[]):
    '''Search for a list of binaries that can be executed from $PATH.

       *names* is a list of exectuable names. *excludes* is a list
       of versions that are concidered false positive and need to be 
       excluded, usually as a result of incompatibilities.

       This function returns a list of absolute paths for the executables
       found and a version number. The version number is retrieved 
       through a command line flag. --version and -V are tried out.

       This function differs from findInclude() and findLib() in its
       search algorithm. findBin() strictly behave like $PATH and
       always returns the FIRST executable reachable from $PATH regardless 
       of version number, unless the version is excluded, in which case
       the result is the same as if the executable hadn't been found.

       Implementation Note:

       *names* and *excludes* are two lists instead of a dictionary
       indexed by executale name for two reasons:
       1. Most times findBin() is called with *names* of executables 
       from the same project. It is cumbersome to specify exclusion 
       per executable instead of per-project.
       2. The prototype of findBin() needs to match the ones of 
       findInclude() and findLib().
    '''
    results = []
    version = None
    for name in names:
        if name.startswith(os.sep):
            # absolute paths only occur when the search has already been
            # executed and completed successfuly.
            continue
        log.write(name + '... ')
        log.flush()
        found = False
        for p in [ context.value('binDir') ] + os.environ['PATH'].split(':'):
            bin = os.path.join(p,name)
            if (os.path.isfile(bin) 
                and os.access(bin, os.X_OK)):
                # We found an executable with the appropriate name,
                # let's find out if we can retrieve a version number.
                for flag in [ '--version', '-V' ]:
                    numbers = []
                    cmdline = [ bin, flag ]
                    cmd = subprocess.Popen(cmdline,
                                           stdout=subprocess.PIPE,
                                           stderr=subprocess.STDOUT)
                    line = cmd.stdout.readline()
                    while line != '':
                        numbers += versionCandidates(line)
                        line = cmd.stdout.readline()
                    cmd.wait()
                    if cmd.returncode != 0:
                        # When the command returns with an error code,
                        # we assume we passed an incorrect flag to retrieve
                        # the version number.
                        numbers = []
                    if len(numbers) > 0:
                        break
                # At this point *numbers* contains a list that can
                # interpreted as versions. Hopefully, there is only
                # one candidate.
                if len(numbers) == 1:
                    excluded = False
                    for exclude in excludes:
                        if ((not exclude[0] 
                             or versionCompare(exclude[0],numbers[0]) <= 0)
                            and (not exclude[1] 
                                 or versionCompare(numbers[0],exclude[1]) < 0)):
                            excluded = True
                            break
                    if not excluded:
                        version = numbers[0]
                        log.write(str(version) + '\n')
                        results.append(bin)
                    else:
                        log.write('excluded (' + str(numbers[0]) + ')\n')
                else:
                    log.write('yes\n')
                    results.append(bin)
                found = True
                break
        if not found:
            log.write('no\n')
    return results, version


def findCache(names):
    '''Search for the presence of files in the cache directory. *names* 
    is a dictionnary of file names used as key and the associated checksum.'''
    results = []
    version = None
    for name in names:
        log.write(name + "... ")
        log.flush()
        fullName = context.cachePath(name)
        if os.path.exists(fullName):
            if names[name]:
                f = open(fullName,'rb')
                sum = hashlib.sha1(f.read()).hexdigest()
                f.close()
                if sum == names[name]:
                    # checksum are matching
                    log.write("cached\n")
                    results += [ fullName ]
                else:
                    log.write("corrupted?\n")
            else:
                log.write("yes\n")
        else:
            log.write("no\n")
    return results, version


def findFiles(base,namePat):
    '''Search the directory tree rooted at *base* for files matching *namePat*
       and returns a list of absolute pathnames to those files.'''
    result = []
    try:
        for p in os.listdir(base):
            path = os.path.join(base,p)
            look = re.match('.*' + namePat + '$',path)
            if look:
                result += [ path ]
            elif os.path.isdir(path):
                result += findFiles(path,namePat)
    except OSError:
        # In case permission to execute os.listdir is denied.
        None
    return result


def findFirstFiles(base,namePat,subdir=''):
    '''Search the directory tree rooted at *base* for files matching pattern
    *namePat* and returns a list of relative pathnames to those files 
    from *base*.
    If ./ is part of pattern, base is searched recursively in breadth search 
    order until at least one result is found.'''
    subdirs = []
    results = []
    patNumSubDirs = len(namePat.split(os.sep))
    subNumSubDirs = len(subdir.split(os.sep))
    for p in os.listdir(os.path.join(base,subdir)):
        relative = os.path.join(subdir,p)
        path = os.path.join(base,relative)
        look = re.match(namePat.replace('.' + os.sep,'(.*)' + os.sep),relative)
        if look != None:
            results += [ relative ]
        elif (((('.' + os.sep) in namePat) 
               or (subNumSubDirs < patNumSubDirs))
              and os.path.isdir(path)):
            # When we see ./, it means we are looking for a pattern 
            # that can be matched by files in subdirectories of the base. 
            subdirs += [ relative ]
    if len(results) == 0:
        for subdir in subdirs:
            results += findFirstFiles(base,namePat,subdir)
    return results

def findData(dir,names,excludes=[]):
    '''Search for a list of extra files that can be found from $PATH
       where bin was replaced by *dir*.'''
    results = []
    for name in names:
        if name.startswith(os.sep):
            # absolute paths only occur when the search has already been
            # executed and completed successfuly.
            continue
        log.write(name + '... ')
        log.flush()
        linkNum = 0
        if name.startswith('.' + os.sep):
            linkNum = len(name.split(os.sep)) - 2
        found = False
        for base in derivedRoots(dir):
            fullNames = findFiles(base,name)
            if len(fullNames) > 0:
                log.write('yes\n')
                tokens = fullNames[0].split(os.sep)
                linked = os.sep.join(tokens[:len(tokens) - linkNum])
                results += [ linked ]
                found = True
                break
        if not found:
            log.write('no\n')
    return results, None

def findEtc(names,excludes=[]):
    return findData('etc',names,excludes)

def findInclude(names,excludes=[]):
    '''Search for a list of libraries that can be found from $PATH
       where bin was replaced by include.

    *names* is list of header filename patterns. *excludes* is a list
    of versions that are concidered false positive and need to be 
    excluded, usually as a result of incompatibilities.
    
    This function returns a list of absolute pathnames to found headers
    and a version number if available.

    This function differs from findBin() and findLib() in its search 
    algorithm. findInclude() might generate a breadth search based 
    out of a derived root of $PATH. It opens found header files
    and look for a "#define.*VERSION" pattern in order to deduce
    a version number.'''
    results = []
    version = None
    includeSysDirs = derivedRoots('include')
    for name in names:
        if name.startswith(os.sep):
            # absolute paths only occur when the search has already been
            # executed and completed successfuly.
            continue
        log.write(name + '... ')
        log.flush()
        found = False
        for includeSysDir in includeSysDirs:
            includes = []
            for header in findFirstFiles(includeSysDir,name):
                # Open the header file and search for all defines
                # that end in VERSION.
                numbers = []
                header = os.path.join(includeSysDir,header)
                f = open(header,'rt')
                line = f.readline()
                while line != '':
                    look = re.match('\s*#define.*VERSION\s+(\S+)',line)
                    if look != None:
                        numbers += versionCandidates(look.group(1))
                    line = f.readline()
                f.close()
                # At this point *numbers* contains a list that can
                # interpreted as versions. Hopefully, there is only
                # one candidate.
                if len(numbers) == 1:
                    excluded = False
                    for exclude in excludes:
                        if ((not exclude[0] 
                             or versionCompare(exclude[0],numbers[0]) <= 0)
                            and (not exclude[1] 
                                 or versionCompare(numbers[0],exclude[1]) < 0)):
                            excluded = True
                            break
                    if not excluded:
                        index = 0
                        for include in includes:
                            if ((not include[1]) 
                                or versionCompare(include[1],numbers[0]) < 0):
                                break
                        includes.insert(index,(header,numbers[0]))
                else:
                    # If we find no version number of find more than one 
                    # version number, we append the header at the end 
                    # of the list with 'None' for version.
                    includes.append((header,None))
            if len(includes) > 0:
                if includes[0][1]:
                    version = includes[0][1]
                    log.write(version + '\n')
                else:
                    log.write('yes\n')
                results.append(includes[0][0])
                includeSysDirs = [ os.path.dirname(includes[0][0]) ]
                found = True
                break
        if not found:
            log.write('no\n')
    return results, version
    

def findLib(names,excludes=[]):
    '''Search for a list of libraries that can be found from $PATH
       where bin was replaced by lib.

    *names* is list of library names with neither a 'lib' prefix 
    nor a '.a', '.so', etc. suffix. *excludes* is a list
    of versions that are concidered false positive and need to be 
    excluded, usually as a result of incompatibilities.
    
    This function returns a list of absolute pathnames to libraries
    found and a version number if available.
    
    This function differs from findBin() and findInclude() in its
    search algorithm. findLib() might generate a breadth search based 
    out of a derived root of $PATH. It uses the full library name
    in order to deduce a version number if possible.'''
    suffix = '.*\.a'                  # Always selects static libraries
    results = []
    version = None
    for name in names:
        if name.startswith(os.sep):
            # absolute paths only occur when the search has already been
            # executed and completed successfuly.
            continue
        log.write(name + '... ')
        log.flush()
        found = False
        for libSysDir in derivedRoots('lib'):
            libs = []
            for libname in findFirstFiles(libSysDir,'lib' + name + suffix):
                numbers = versionCandidates(libname)
                if len(numbers) == 1:
                    excluded = False
                    for exclude in excludes:
                        if ((not exclude[0] 
                             or versionCompare(exclude[0],numbers[0]) <= 0)
                            and (not exclude[1] 
                                 or versionCompare(numbers[0],exclude[1]) < 0)):
                            excluded = True
                            break
                    if not excluded:
                        index = 0
                        for lib in libs:
                            if ((not lib[1]) 
                                or versionCompare(lib[1],numbers[0]) < 0):
                                break
                        libs.insert(index,(os.path.join(libSysDir,libname),
                                           numbers[0]))
                else:
                    libs.append((os.path.join(libSysDir,libname),None))
            if len(libs) > 0:
                if libs[0][1]:
                    version = libs[0][1] 
                    look = re.match('.*lib' + name + '(.+)',libs[0][0])
                    if look:
                        suffix = look.group(1)
                    log.write(suffix + '\n')
                else:
                    log.write('yes\n')
                results.append(libs[0][0])
                found = True
                break
        if not found:
            log.write('no\n')
    return results, version


def findPrerequisites(deps, excludes=[]):
    '''Find a set of executables, headers, libraries, etc. on a local machine.
    
    *deps* is a dictionary where each key associates an install directory 
    (bin, include, lib, etc.) to file names (executable, header, library, 
    etc.). *excludes* contains a list of excluded version ranges.

    This function will try to find the latest version of each file which 
    was not excluded.

    This function will return a dictionnary matching *deps* where each found
    file will be replaced by an absolute pathname and each file not found
    will not be present. This function returns True if all files in *deps* 
    can be fulfilled and returns False if any file cannot be found.'''
    import __main__
    version = None
    installed = {}
    complete = True
    for dir in [ 'bin', 'include', 'lib', 'etc', 'share' ]:
        # The search order "bin, include, lib, etc" will determine 
        # how excluded versions apply.
        if dir in deps:
            command = 'find' + dir.capitalize()
            installed[dir], installedVersion = \
                __main__.__dict__[command](deps[dir],excludes)
            # Once we have selected a version out of the installed
            # local system, we lock it down and only search for
            # that specific version.
            if not version and installedVersion:
                version = installedVersion
                excludes = [ (None,version), (versionIncr(version),None) ]
            if len(installed[dir]) != len(deps[dir]):
                complete = False

    return installed, complete

def findShare(names,excludes=[]):
    return findData('share',names,excludes)

def fetch(filenames, cacheDir=None, force=False):
    '''download file from remote server.'''
    if len(filenames) > 0:
        if force:
            downloads = filenames
        else:
            locals = findCache(filenames)
            downloads = []
            for filename in filenames:
                if not context.cachePath(filename) in locals:
                    dir = os.path.dirname(context.cachePath(filename))
                    if not os.path.exists(dir):
                        os.makedirs(dir)
                    downloads += [ filename ]
        cmdline = "rsync -avuzb"
        if not cacheDir:
            cacheDir = context.cachePath('')
            cmdline = cmdline + 'R'
        remotePath = context.remoteCachePath('')
        look = re.match('(\S+@)?(\S+):(\S+)',remotePath)
        if look:
            username = look.group(1)
            hostname = look.group(2)
            dirname = look.group(3)
            sources = "'" + context.remoteCachePath('') + './' \
                +' ./'.join(downloads).replace(' ',' ' + dirname + os.sep) + "'"
            cmdline = cmdline + " --rsh=ssh"
            if username:
                cmdline = cmdline + username
        else:
            sources = context.remoteCachePath('') + './' \
                +' ./'.join(downloads).replace(' ',' ' + remotePath + os.sep)
        cmdline = cmdline + ' ' + sources + ' ' + cacheDir
        shellCommand(cmdline)


def install(packages, extraFetches={}, dbindex=None, force=False):
    '''install a pre-built (also pre-fetched) package.
    '''
    if not dbindex:
        dbindex = index
    dbindex.validate(force)
    handler = Unserializer(packages)
    dbindex.parse(handler)

    if len(extraFetches) > 0:
        fetch(extraFetches)

    # If an error occurs, at least save previously configured variables.
    context.save()

    managed = []
    for name in packages:
        # *name* is definitely handled by the local system package manager
        # whenever there is no associated project.
        if name in handler.projects:
            package = handler.asProject(name).package
            if package and os.path.exists(context.cachePath(package.name)):
                # The package is not part of the local system package manager
                # though it has been pre-built.
                if context.host() == 'Darwin':
                    installDarwinPkg(context.cachePath(package.name),
                                     context.value('darwinTargetVolume'))
                elif context.host() == 'Ubuntu':
                    shellCommand('dpkg -i ' + context.cachePath(package.name),
                                 admin=True)
                else:
                    raise Error("Does not know how to install '" \
                                    + package.name + "'" )
            else:
                managed += [ name ]
        else:
            managed += [ name ]

    if len(managed) > 0:
        if context.host() == 'Ubuntu':
            # Add DEBIAN_FRONTEND=noninteractive such that interactive
            # configuration of packages do not pop up in the middle 
            # of installation. We are going to update the configuration
            # in /etc afterwards anyway.
            shellCommand('apt-get update', admin=True)
            shellCommand('DEBIAN_FRONTEND=noninteractive apt-get -y install '\
                             + ' '.join(packages),admin=True)
        elif context.host() == 'Darwin':
            shellCommand('port install ' + ' '.join(packages),admin=True)
        else:
            raise Error("Use of package manager for '" \
                            + context.host() + " not yet implemented.'")


def installDarwinPkg(image,target,pkg=None):
    '''Mount *image*, a pathnme to a .dmg file and use the Apple installer 
    to install the *pkg*, a .pkg package onto the platform through the Apple 
    installer.'''
    base, ext = os.path.splitext(image)
    volume = os.path.join('/Volumes',os.path.basename(base))
    shellCommand('hdiutil attach ' + image)
    if target != 'CurrentUserHomeDirectory':
        message = 'ATTENTION: You need administrator privileges on ' \
                + 'the local machine to execute the following cmmand\n'
        if log:
            log.write(message)
        else:
            sys.stdout.write(message)
        admin = True
    else:
        admin = False
    if not pkg:
        pkgs = findFiles(volume,'\.pkg')
        if len(pkgs) != 1:
            raise RuntimeError('ambiguous: not exactly one .pkg to install')
        pkg = pkgs[0]
    cmdline = 'installer -pkg ' + os.path.join(volume,pkg) \
            + ' -target "' + target + '"'
    shellCommand(cmdline,admin)
    shellCommand('hdiutil detach ' + volume)


def linkDependencies(projects, cuts=[]):
    '''All projects which are dependencies but are not part of *srcTop*
    are not under development in the current workspace. Links to 
    the required executables, headers, libraries, etc. will be added to 
    the install directories such that projects in *srcTop* can build.'''
    import __main__

    missings = []
    for project in projects:
        for prereq in projects[project].buildDeps:
            if not prereq in cuts:
                # First, we will check if findPrerequisites needs to be rerun.
                # It is the case if the link in [bin|include|lib|...]Dir does
                # not exist and the pathname for it in buildDeps is not 
                # an absolute path.  
                complete = True
                deps = projects[project].buildDeps[prereq]
                for dir in deps:
                    for path in deps[dir]:
                        command = 'linkPath' + dir.capitalize()
                        if dir == 'lib':
                            path = 'lib' + path + '.a'
                        linkName = __main__.__dict__[command](path)
                        if not (path.startswith(os.sep)
                                or os.path.exists(linkName)):
                            complete = False
                if not complete:
                    projects[project].buildDeps[prereq], 
                    complete = findPrerequisites(
                        projects[project].buildDeps[prereq],
                        projects[project].buildExcludes[prereq])
                if not complete and not prereq in missings:
                    missings += [ prereq ]
                else:
                    for install in projects[project].buildDeps[prereq]:
                        context.linkPath(
                            projects[project].buildDeps[prereq][install],
                            install + 'Dir')
    if len(missings) > 0:
        raise Error("incomplete prerequisites for " + ' '.join(missings),1)


def linkPathBin(path):
    return os.path.join(context.value('binDir'),
                        os.path.basename(path))

def linkPathEtc(path):
    return os.path.join(context.value('etcDir'),
                        os.path.basename(path))

def linkPathInclude(path):
    dirname, header = os.path.split(path)
    if dirname != 'include':
        header = os.path.basename(dirname)
    return os.path.join(context.value('includeDir'),header)

def linkPathLib(path):
    libname, libext = os.path.splitext(os.path.basename(path))
    libname = libname.split('-')[0]
    libname = libname + libext
    return os.path.join(context.value('libDir'),libname)

def linkPathShare(path):
    return os.path.join(context.value('shareDir'),
                        os.path.basename(path))

def make(names, targets):
    '''invoke the make utility to build a set of projects.'''
    if 'recurse' in targets:
        targets.remove('recurse')
        # Recurse through projects that need to be rebuilt first 
        # If no targets have been specified, the default target is to build
        # projects. Each project in turn has thus to be installed in order
        # to build the next one in the topological chain.
        recursiveTargets = targets
        if len(recursiveTargets) == 0:
            recursiveTargets = [ 'install' ]
        names, projects = validateControls(names)
        # We will generate a "make install" for all projects which are 
        # a prerequisite. Those Makefiles expects bin, include, lib, etc.
        # to be defined.
        for dir in [ 'bin', 'include', 'lib', 'etc', 'share', 'log' ]:
            name = context.value(dir + 'Dir')
        last = names.pop()
        for name in names:
            # Dependencies which are concidered to be packages have files 
            # located anywhere on the local system and only links to those
            # files end-up in build{Bin,Lib,etc.}. 
            # Those links cannot be created in validateControls though since
            # we also have "package patches projects", i.e. projects which
            # are only there as temporary workarounds for packages which 
            # will be coming out of the local system package manager at some
            # point in the future.
            linkDependencies({ name: projects[name]})
            makeProject(name,recursiveTargets)
        # Make current project
        linkDependencies({ last: projects[last]})
        if len(targets) > 0:
            makeProject(last,targets)
    else:
        for name in names:
            makeProject(name,targets)


def makeProject(name,targets):
    '''Issue make command and log output.'''
    status = 'compile'
    log.header(name)
    makefile = context.srcDir(os.path.join(name,'Makefile'))
    objDir = context.objDir(name)
    if objDir != os.getcwd():
        if not os.path.exists(objDir):
            os.makedirs(objDir)
        os.chdir(objDir)
    try:
        cmdline = 'export PATH=' + context.value('binDir') \
            + ':${PATH} ; make -f ' + makefile
        if len(targets) > 0:
            cmdline = cmdline + ' ' + ' '.join(targets)
            shellCommand(cmdline)
            status = targets[0]
            if len(targets) > 1:
                status = status + '...'
        else:
            shellCommand(cmdline)
            status = 'build'
    except Error, e:
        log.error(str(e))
    log.footer(status)


def mergeBuildConf(dbPrev,dbUpd,parser):
    '''Merge an updated project dependency database into an existing
       project dependency database. The existing database has been
       augmented by user-supplied information such as "use source
       controlled repository", "skip version X dependency", etc. Hence
       we do a merge instead of a complete replace.'''
    if dbPrev == None:
        return dbUpd
    elif dbUpd == None:
        return dbPrev
    else:
        # We try to keep user-supplied information in the prev
        # database whenever possible.
        # Both databases supply packages in alphabetical order,
        # so the merge can be done in a single pass.
        dbNext = tempfile.TemporaryFile()
        projPrev = parser.copy(dbNext,dbPrev)
        projUpd = parser.next(dbUpd)
        while projPrev != None and projUpd != None:
            if projPrev < projUpd:
                parser.startProject(dbNext,projPrev)
                projPrev = parser.copy(dbNext,dbPrev)
            elif projPrev > projUpd:
                parser.startProject(dbNext,projUpd)
                projUpd = parser.copy(dbNext,dbUpd)
            elif projPrev == projUpd:
                # when names are equals, we need to import user-supplied
                # information as appropriate. For now, there are only one
                # user supplied-information, the install mode for the package.
                # Package name is a unique key so we can increment
                # both iterators.
                parser.startProject(dbNext,projUpd)
                #installMode, version = parser.installMode(projPrev)
                #parser.setInstallMode(dbNext,installMode,version)
                # It is critical this line appears after we set the installMode
                # because it guarentees that the install mode will always be
                # the first line after the package tag.
                projUpd = parser.copy(dbNext,dbUpd,True)
                projPrev = parser.copy(dbNext,dbPrev)
        while projPrev != None:
            parser.startProject(dbNext,projPrev)
            projPrev = parser.copy(dbNext,dbPrev)
        while projUpd != None:
            parser.startProject(dbNext,projUpd)
            projUpd = parser.copy(dbNext,dbUpd)
        parser.trailer(dbNext)
        return dbNext


def searchBackToRoot(filename,root=os.sep):
    '''Search recursively from the current directory to the *root*
    of the directory hierarchy for a specified *filename*.
    This function returns the relative path from *filename* to pwd
    and the absolute path to *filename* if found.'''
    d = os.getcwd()
    dirname = '.'
    while (not os.path.samefile(d,root) 
           and not os.path.isfile(os.path.join(d,filename))):
        if dirname == '.':
            dirname = os.path.basename(d)
        else:
            dirname = os.path.join(os.path.basename(d),dirname)
        d = os.path.dirname(d)
    if not os.path.isfile(os.path.join(d,filename)):
        raise IOError(1,"cannot find file",filename)
    return dirname, os.path.join(d,filename)


def shellCommand(commandLine, admin=False):
    '''Execute a shell command and throws an exception when the command fails'''
    if admin:
        cmdline = 'sudo ' + commandLine
    else:
        cmdline = commandLine
    if log:
        log.write(cmdline + '\n')
        log.flush()
    else:
        sys.stdout.write(cmdline + '\n')
    cmd = subprocess.Popen(cmdline,shell=True,
                           stdout=subprocess.PIPE,
                           stderr=subprocess.STDOUT)
    line = cmd.stdout.readline()
    while line != '':
        if log:
            log.write(line)
        else:
            sys.stdout.write(line)
        line = cmd.stdout.readline()
    cmd.wait()
    if cmd.returncode != 0:
        raise Error("unable to complete: " + cmdline + '\n',cmd.returncode)


def sortBuildConfList(dbPathnames,parser):
    '''Sort/Merge projects defined in a list of files, *dbPathnames*.
    *parser* is the parser used to read the projects files in.'''
    dbPrev = None
    dbUpd = None
    if len(dbPathnames) == 0:
        return None
    elif len(dbPathnames) == 1:
        dbPrev = open(dbPathnames[0])
        return dbPrev
    elif len(dbPathnames) == 2:
        dbPrev = open(dbPathnames[0])
        dbUpd = open(dbPathnames[1])
    else:
        dbPrev = sortBuildConfList(dbPathnames[:len(dbPathnames) / 2],parser)
        dbUpd = sortBuildConfList(dbPathnames[len(dbPathnames) / 2:],parser)
    dbNext = mergeBuildConf(dbPrev,dbUpd,parser)
    dbNext.seek(0)
    dbPrev.close()
    dbUpd.close()
    return dbNext


def validateControls(repositories, dbindex=None, force=False):
    '''Checkout source code files, install packages such that 
    the projects specified in *repositories* can be built.
    *dbindex* is the project index that contains the dependency 
    information to use. If None, the global index fetched from
    the remote machine will be used.

    This function returns a topologicaly sorted list of projects
    in *srcTop* and an associated dictionary of Project instances. 
    By iterating through the list, it is possible to 'make' 
    each prerequisite project in order.'''
    if not dbindex:
        dbindex = index
    dbindex.validate(force)
    dgen = MakeGenerator(repositories,excludePats) # excludePats is global.
    missingPackages = []
    missingControls = []
    for project in repositories:
        # Check only for existance of directory, else "make dist-src"
        # will try to download repository from server.
        # if not context.isControlled(project):
        if not os.path.exists(context.srcDir(project)):
            missingControls += [ project ]

    # Add deep dependencies
    while len(dgen.vertices) > 0:
        controls = []
        dbindex.parse(dgen)
        if len(dgen.missings) > 0:
            # This is an opportunity to prompt for missing dependencies.
            # After installing both, source controlled and packaged
            # projects, the checked-out projects will be added to 
            # the dependency graph while the packaged projects will
            # be added to the *cut* list.
            controls, packages = dgen.candidates(missingControls \
                                                     + missingPackages)
            controls, packages = selectCheckout(controls,packages)
            for control in controls:
                missingControls += [ control ]
            for package in packages:
                missingPackages += [ package ]
        dgen.nextLevel(missingControls)
    # Checkout missing source controlled projects
    # and install missing packages.
    install(missingPackages,dgen.extraFetches,dbindex)
    if force:
        # Force update all projects under revision control
        update(dgen.topological(),dgen.extraFetches,dbindex,force)
    else:
        # Update only projects which are missing from *srcTop*
        # and leave other projects in whatever state they are in.
        update(missingControls,dgen.extraFetches,dbindex,force)
    return dgen.topological(), dgen.projects


def versionCandidates(line):
    '''Extract patterns from *line* that could be interpreted as a 
    version numbers. That is every pattern that is a set of digits
    separated by dots and/or underscores.'''
    part = line
    candidates = []
    while part != '':
        # numbers should be full, including '.'
        # look = re.match('[^0-9]*([0-9][0-9_\.]*)+(.*)',part)
        look = re.match('[^0-9]*([0-9].*)',part)
        if look:
            part = look.group(1)
            look = re.match('[^0-9]*([0-9]+([_\.][0-9]+)+)+(.*)',part)
            if look:
                candidates += [ look.group(1) ]
                part = look.group(2)
            else:
                while (len(part) > 0
                       and part[0] in ['0', '1', '2', '3', '4', '5', 
                                       '6', '7', '8', '9' ]):
                    part = part[1:]
        else:
            part = ''
    return candidates


def versionCompare(left,right):
    '''Compare version numbers

    This function returns -1 if a *left* is less than *right*, 0 if *left 
    is equal to *right* and 1 if *left* is greater than *right*.
    It is suitable as a custom comparaison function for sorted().'''
    leftRemain = left.replace('_','.').split('.')
    rightRemain = right.replace('_','.').split('.')
    while len(leftRemain) > 0 and len(rightRemain) > 0:
        leftNum = leftRemain.pop(0)
        rightNum = rightRemain.pop(0)
        if leftNum < rightNum:
            return -1
        elif leftNum > rightNum:
            return 1
    if len(leftRemain) < len(rightRemain):
        return -1
    elif len(leftRemain) > len(rightRemain):
        return 1
    return 0


def versionIncr(v):
    '''returns the version number with the smallest increment 
    that is greater than *v*.'''
    return v + '.1'


def upstreamRecurse(srcdir,pchdir):
    for name in os.listdir(pchdir):
        srcname = os.path.join(srcdir,name)
        pchname = os.path.join(pchdir,name)
        if os.path.isdir(name):
            upstreamRecurse(srcname,pchname)
        else:
            if os.path.islink(srcname):
                os.unlink(srcname)
            if os.path.isfile(srcname + '.patched'):
                shutil.copy(srcname + '.patched',srcname)


def integrate(srdir,pchdir):
    for name in os.listdir(pchdir):
        srcname = os.path.join(srcdir,name)
        pchname = os.path.join(pchdir,name)
        if os.path.isdir(name):
            if not name.endswith('CVS'):
                integrate(srcname,pchname)
        else:
            if not name.endswith('~'):
                if not os.path.islink(srcname):
                    if os.path.isfile(srcname):
                        shutil.move(srcname,srcname + '.patched')
                    os.symlink(os.path.relpath(pchname),srcname)


def update(controls, extraFetches={}, dbindex = None, force=False):
    '''Update a list of *projects* within the workspace. The update will either 
    sync with a source control repository if the project is present in *srcTop*
    or will install a new binary package through the local package manager.
    *extraFetches* is a list of extra files to fetch from the remote machine,
    usually a list of compressed source tar files.'''
    if not dbindex:
        dbindex = index
    dbindex.validate(force)
    handler = Unserializer(controls)
    dbindex.parse(handler)

    if len(extraFetches) > 0:
        fetch(extraFetches)

    # If an error occurs, at least save previously configured variables.
    context.save()
    for name in controls:
        # The project is present in *srcTop*, so we will update the source 
        # code from a repository. 
        control = handler.asProject(name).control
        if control:
            # Not every project is made a first-class citizen. If there are 
            # no control structure for a project, it must depend on a project
            # that does in order to have a source controlled repository.
            # This is a simple way to specify inter-related projects 
            # with complex dependency set and barely any code. 
            if control.type == 'git-core':
                if not os.path.exists(
                          os.path.join(context.srcDir(name),'.git')):
                    # If the path to the remote repository is not absolute,
                    # derive it from *remoteTop*. Binding any sooner will 
                    # trigger a potentially unnecessary prompt for remotePath.
                    if not ':' in control.url and context:
                        control.url = context.remoteSrcPath(control.url)
                    cmdline = 'git clone ' + control.url \
                        + ' ' + context.srcDir(name)
                    shellCommand(cmdline)
                else:
                    cwd = os.getcwd()
                    os.chdir(context.srcDir(name))
                    cmdline = 'git pull'
                    shellCommand(cmdline)
                    cmdline = 'git checkout -m'
                    shellCommand(cmdline)
                    os.chdir(cwd)
            else:
                raise Error("unknown source control system '"  \
                                + control.type + "'")
        else:
            sys.stdout.write('warning: ' + name + ' is not a project under source control. It is most likely a psuedo-project and will be updated through an "update recurse" command.\n')
                             
            
def upstream(srcdir,pchdir):
    upstreamRecurse(srcdir,pchdir)
    #subprocess.call('diff -ru ' + srcdir + ' ' + pchdir,shell=True)
    p = subprocess.Popen('diff -ru ' + srcdir + ' ' + pchdir, shell=True,
              stdout=subprocess.PIPE, close_fds=True)
    line = p.stdout.readline()
    while line != '':
        look = re.match('Only in ' + srcdir + ':',line)
        if look == None:
            log.write(line)
        line = p.stdout.readline()
    p.poll()
    integrate(srcdir,pchdir)


def pubBuild(args):
    '''build                  [remoteTop [localTop]]
                        This bootstrap command will download the index 
                        database from *remoteTop* and starts rebuilding 
                        every project listed. When both *remoteTop* and 
                        *localTop*, the root on the local machine where 
                        sources and object files are stored, are specified, 
                        the script runs to completion with no interactive 
                        prompt. It is a useful feature for using the script 
                        in cron jobs on build servers.
    '''
    if len(args) > 0:
        context.remoteCacheTop.default = args[0]
    if len(args) > 1:
        context.cacheTop.default = args[1]
    global useDefaultAnswer
    useDefaultAnswer = True
    global log
    log = LogFile(context.logname())
    rgen = DerivedSetsGenerator()
    index.parse(rgen)
    make(rgen.repositories,[ 'recurse', 'install', 'dist-src' ])
    pubCollect([])


def pubCollect(args):
    '''collect                Consolidate local dependencies information 
                       into a glabal dependency database. Copy all 
                       distribution packages built into a platform 
                       distribution directory.
    '''

    # Create the distribution directory, i.e. where packages are stored.
    packageDir = context.cachePath(context.host())
    if not os.path.exists(packageDir):
        os.makedirs(packageDir)
    srcPackageDir = context.cachePath('srcs')
    if not os.path.exists(srcPackageDir):
        os.makedirs(srcPackageDir)

    # Create the project index file
    # and copy the packages in the distribution directory.
    extensions = { 'Darwin': ('\.dsx', '\.dmg'),
                   'Fedora': ('.spec', '\.rpm'), 
                 }
    indices = []
    # collect source packages
    copySrcPackages = 'rsync ' + context.dbPathname() + ' ' \
                 + ' '.join(findFiles(context.value('buildTop'),'.tar.bz2')) \
                 + ' ' + srcPackageDir
    copyBinPackages = 'rsync ' + context.dbPathname() + ' '
    if context.host() in extensions:
        ext = extensions[context.host()]
        indices = findFiles(context.value('buildTop'),ext[0])
        copyBinPackages = copyBinPackages \
                     + ' '.join(findFiles(context.value('buildTop'),ext[1]))
    copyBinPackages = copyBinPackages + ' ' + packageDir
    indices += findFiles(context.value('srcTop'),'index.xml')
    createIndexPathname(context.dbPathname(),indices)
    # We should only copy the index file after we created it.
    shellCommand(copyBinPackages)
    shellCommand(copySrcPackages)


def pubConfigure(args):
    '''configure              Configure the local machine with direct 
                       dependencies of a project such that the project 
                       can be built later on.
    '''
    global log 
    log = LogFile(context.logname())
    projectName = context.cwdProject()
    # \todo should report missing *direct* dependencies without install them. 
    validateControls([ projectName ],
                     IndexProjects(context,
                     context.srcDir(os.path.join(projectName,'index.xml'))))


def pubContext(args):
    '''context                Prints the absolute pathname to a file.
                       If the filename cannot be found from the current 
                       directory up to the workspace root (i.e where ws.mk 
                       is located), it assumes the file is in *etcDir*.
    '''
    pathname = context.configFilename
    if len(args) >= 1:
        try:
            dir, pathname = searchBackToRoot(args[0],
                   os.path.dirname(context.configFilename))
        except IOError:
            pathname = os.path.join(context.value('etcDir'),args[0])
    sys.stdout.write(pathname)


def pubInit(args):
    '''init                   Prompt for variables which have not been 
                       initialized in ws.mk. Fetch the project index.
    '''
    found = False
    for d in context.environ.values():
        found |= selectVariable(d)
    if found:
        context.save()
    index.validate()

def pubInstall(args):
    '''install                Install a package on the local system.
    '''
    install(args)

def pubIntegrate(args):
    '''integrate              Integrate a patch into a source package
    '''
    while len(sys.argv) > 0:
        srcdir = sys.argv.pop(0)
        pchdir = srcdir + '-patch'
        integrate(srcdir,pchdir)


def pubHost(args):
    '''host                   Host platform used to build the workspace.
                       This will display the distribution name on
                       stdout.
    '''
    print context.host()


class ListPdbHandler(PdbHandler):

    def startProject(self, name):
        sys.stdout.write(name)

    def description(self, text):
        sys.stdout.write(text)

    def endProject(self):
        sys.stdout.write('\n')


def pubList(args):
    '''list                   List available projects
    '''
    parser = xmlDbParser()
    parser.parse(context.dbPathname(),ListPdbHandler())


def pubMake(args):
    '''make                   Make projects. "make recurse" will build 
                       all dependencies required before a project 
                       can be itself built.
    '''
    global log 
    log = LogFile(context.logname())
    repositories = [ context.cwdProject() ]
    make(repositories,args)


def pubUpdate(args):
    '''update                 Update projects installed in the workspace
    '''
    global log 
    log = LogFile(context.logname())
    reps = args
    recurse = False
    if 'recurse' in args:
        recurse = True
        reps.remove('recurse')
    if len(reps) == 0:
        # We try to derive project names from the current directory whever 
        # it is a subdirectory of buildTop or srcTop.
        srcDir = os.path.realpath(os.getcwd()).replace(
            context.value('buildTop'),
            context.value('srcTop'))
        for repdir in findFiles(srcDir,'\.git'):
            reps += [ os.path.dirname(repdir.replace(context.value('srcTop') \
                                                         + os.sep,'')) ]
    if recurse:
        names, projects = validateControls(reps,force=True)
    else:
        update(reps,force=True)
    

def pubUpstream(args):
    '''upstream               Generate a patch to submit to upstream 
                       maintainer out of a source package and 
                       a repository.
    '''
    while len(sys.argv) > 0:
        srcdir = sys.argv.pop(0)
        pchdir = srcdir + '-patch'
        upstream(srcdir,pchdir)


def selectCheckout(controlCandidates,packageCandidates=[]):
    '''Interactive prompt for a selection of projects to checkout.
    *controlCandidates* contains a list of rows describing projects available
    for selection. This function will return a list of projects to checkout
    from a source repository and a list of projects to install through 
    a package manager.'''
    controls = []
    packages = []
    if len(controlCandidates) > 0:
        controls = selectMultiple(
'''The following dependencies need to be present on your system. 
You have now the choice to install them from a source repository. You will later
have  the choice to install them from binary package or not at all.''',
        controlCandidates)

        # Filters out the dependencies that should be installed from a source 
        # repository from the list of candidates to install as binary packages.
        for row in controlCandidates:
            if not row[0] in controls:
                packageCandidates += [ row ]
    packages = selectInstall(packageCandidates)
    return controls, packages


def selectInstall(packageCandidates):
    '''Interactive prompt for a selection of projects to install 
    as binary packages. *packageCandidates* contains a list of rows 
    describing projects available for selection. This function will 
    return a list of projects to install through a package manager. '''
    packages = []
    if len(packageCandidates) > 0:
        packages = selectMultiple(
    '''The following dependencies need to be present on your system. 
You have now the choice to install them from a binary package. You can skip
this step if you know those dependencies will be resolved correctly later on.
''',packageCandidates)
    return packages


def selectOne(description,choices):
    '''Prompt an interactive list of choices and returns the element selected
    by the user. *description* is a text that explains the reason for the 
    prompt. *choices* is a list of elements to choose from. Each element is 
    in itself a list. Only the first value of each element is of significance
    and returned by this function. The other values are only use as textual
    context to help the user make an informed choice.'''
    choice = None
    while True:
        showMultiple(description,choices)
        if useDefaultAnswer:
            selection = "1"
        else:
            selection = raw_input("Enter a single number [1]: ")
            if selection == "":
                selection = "1"
        try:
            choice = int(selection)
            if choice >= 1 and choice <= len(choices):
                return choices[choice - 1][0]
        except TypeError:
            choice = None
        except ValueError:  
            choice = None
    return choice


def selectMultiple(description,selects):
    '''Prompt an interactive list of choices and returns elements selected
    by the user. *description* is a text that explains the reason for the 
    prompt. *choices* is a list of elements to choose from. Each element is 
    in itself a list. Only the first value of each element is of significance
    and returned by this function. The other values are only use as textual
    context to help the user make an informed choice.'''
    result = []
    done = False
    choices = [ [ 'all' ] ] + selects
    while len(choices) > 1 and not done:
        showMultiple(description,choices)
        sys.stdout.write(str(len(choices) + 1) + ')  done\n')
        if useDefaultAnswer:
            selection = "1"
        else:
            selection = raw_input("Enter a list of numbers separated by spaces [1]: ")
            if len(selection) == 0:
                selection = "1"
        # parse the answer for valid inputs
        selection = selection.split(' ')
        for s in selection:
            try:
                choice = int(s)
            except TypeError:
                choice = 0
            except ValueError:  
                choice = 0
            if choice > 1 and choice <= len(choices):
                result += [ choices[choice - 1][0] ]
            elif choice == 1:
                result = []
                for c in choices[1:]:
                    result += [ c[0] ] 
                done = True
            elif choice == len(choices) + 1:
                done = True
        # remove selected items from list of choices
        remains = []
        for row in choices:
            if not row[0] in result:
                remains += [ row ]
        choices = remains
    return result


def selectVariable(d):
    '''Generate an interactive prompt to enter a workspace variable 
    *var* value and returns True if the variable value as been set.'''
    found = False
    if not d.value:
        found = True
        sys.stdout.write('\n' + d.name + ':\n')
        if isinstance(d,Pathname):
            sys.stdout.write(d.descr + '\n')
            # compute the default leaf directory from the variable name 
            leafDir = d.name
            for last in range(0,len(d.name)):
                if d.name[last] in 'ABCDEFGHIJKLMNOPQRSTUVWXYZ':
                    leafDir = d.name[:last]
                    break
            dir = d
            default = d.default
            if (not default 
                or (not (':' in default) or default.startswith(os.sep))):
                # If there are no default values or the default is not
                # an absolute pathname.
                if d.base:
                    if default:
                        showDefault = '*' + d.base.name + '*/' + default
                    else:
                        showDefault = '*' + d.base.name + '*/' + leafDir
                    if not d.base.value:
                        directly = 'Enter *' + d.name + '* directly ?'
                        offbase = 'Enter *' + d.base.name + '*, *' + d.name \
                                     + '* will defaults to ' + showDefault + ' ?'
                        selection = selectOne(d.name + ' is based on *' + d.base.name \
                            + '* by default. Would you like to ... ',
                                  [ [ offbase  ],
                                    [ directly ] ])
                        if selection == offbase:
                            dir = d.base
                            default = dir.default
                    else:
                        if default:
                            default = os.path.join(d.base.value,default)
                        else:
                            default = os.path.join(d.base.value,leafDir)
                elif default:
                    default = os.path.join(os.getcwd(),default)
            if not default:
                default = os.getcwd()

            if useDefaultAnswer:
                dirname = default
            else:
                dirname = raw_input("Enter a pathname [" + default + "]: ")
            if dirname == '':
                dirname = default
            if not ':' in dirname:
                dirname = os.path.normpath(os.path.abspath(dirname))
            dir.value = dirname
            if dir != d:
                if d.default:
                    d.value = os.path.join(d.base.value,d.default)
                else:
                    d.value = os.path.join(d.base.value,leafDir)
            if not ':' in dirname:
                if not os.path.exists(d.value):
                    sys.stdout.write(d.value + ' does not exist.\n')
                    os.makedirs(d.value)
        elif isinstance(d,SingleChoice):
            d.value = selectOne(d.descr,d.choices)
    return found


def selectYesNo(description):
    '''Prompt for a yes/no answer.'''
    if useDefaultAnswer:
        return True
    yesNo = raw_input(description + " [Y/n]? ")
    if yesNo == '' or yesNo == 'Y' or yesNo == 'y':
        return True
    return False


def showMultiple(description,choices):
    '''Display a list of choices on the user interface.'''
    # Compute display layout
    item = 1
    widths = []
    displayed = []
    for row in choices:
        c = 0
        row = [ str(item) + ')' ] + row
        displayed += [ row ]
        item = item + 1
        for col in row:
            if len(widths) <= c:
                widths += [ 2 ]
            widths[c] = max(widths[c],len(col) + 2)
            c = c + 1
    # Ask user to review selection
    sys.stdout.write(description + '\n')
    for project in displayed:
        c = 0
        for col in project:
            sys.stdout.write(col.ljust(widths[c]))
            c = c + 1
        sys.stdout.write('\n')


# Main Entry Point
if __name__ == '__main__':
    try:
        import __main__
	import optparse

        epilog= ''
        d = __main__.__dict__
        keys = d.keys()
        keys.sort()
        for command in keys:
            if command.startswith('pub'):
                epilog += __main__.__dict__[command].__doc__ + '\n'

	parser = optparse.OptionParser(usage="Usage: %prog [options] command",
                                       formatter=CommandsFormatter(),
                                       epilog=epilog)
	parser.add_option('--default', dest='default', action='store_true',
	    help='Use default answer for every interactive prompt.')
	parser.add_option('--exclude', dest='excludePats', action='append',
	    help='The specified command will not be applied to projects matching the name pattern.')
	parser.add_option('--version', dest='version', action='store_true',
	    help='Print version information')
        
	options, args = parser.parse_args()
	if options.version:
		sys.stdout.write('dws version ' + __version__ + '\n')
		sys.exit(0)
        useDefaultAnswer = options.default
        if options.excludePats:
            excludePats = options.excludePats

        if len(args) < 1:
            parser.print_help()
            sys.exit(1)

        # Find the build information
        arg = args.pop(0)
        context = Context()
        index = IndexProjects(context)
        command = 'pub' + arg.capitalize()
        if command in __main__.__dict__:
            __main__.__dict__[command](args)
        else:
            raise Error(sys.argv[0] + ' ' + arg + ' does not exist.\n')

    except Error, err:
        if log:
            log.error(str(err))
        else:
            sys.stderr.write(str(err))
        sys.exit(err.code)

    if log:
        log.close()