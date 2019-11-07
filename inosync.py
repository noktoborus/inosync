#!/usr/bin/python
# vim: set fileencoding=utf-8 ts=2 sw=2 expandtab :

import os,sys
from optparse import OptionParser,make_option
from syslog import *
from pyinotify import *

# TODO: per-thread directory subscribe

__author__ = "Benedikt Böhm"
__copyright__ = "Copyright (c) 2007-2008 Benedikt Böhm <bb@xnull.de>"
__version__ = 0,2,3

OPTION_LIST = [
  make_option(
      "-c", dest = "config",
      default = "/etc/inosync/default.py",
      metavar = "FILE",
      help = "load configuration from FILE"),
  make_option(
      "-d", dest = "daemonize",
      action = "store_true",
      default = False,
      help = "daemonize %prog"),
  make_option(
      "-p", dest = "pretend",
      action = "store_true",
      default = False,
      help = "do not actually call rsync"),
  make_option(
      "-s", dest = "sched",
      action = "store_true",
      default = False,
      help = "schedule rsync calls"),
  make_option(
      "-v", dest = "verbose",
      action = "store_true",
      default = False,
      help = "print debugging information"),
]

DEFAULT_EVENTS = [
    "IN_CLOSE_WRITE",
    "IN_CREATE",
    "IN_DELETE",
    "IN_MOVED_FROM",
    "IN_MOVED_TO"
]

# url-to-time list
LAST_EVENT = {
    }

def call(cmd):
  syslog(LOG_DEBUG, "executing %s" % cmd)
  proc = os.popen(cmd)
  for line in proc:
    syslog(LOG_DEBUG, "[rsync] %s" % line.strip())

def sched():
  ctime = time.time()
  for ev_cmd, ev_time in LAST_EVENT.items():
    if ctime + 0.100 > ev_time:
      call(ev_cmd)
      LAST_EVENT.pop(ev_cmd)

class RsyncEvent(ProcessEvent):
  pretend = None
  sched = None

  def __init__(self, pretend=False, sched=False):
    self.pretend = pretend
    self.sched = sched

  def sync(self, wpath):
    args = [config.rsync, "-ltrp", "--delete"]
    if config.extra:
      args.append(config.extra)
    args.append("--bwlimit=%s" % config.rspeed)
    if config.logfile:
      args.append("--log-file=%s" % config.logfile)
    if "rexcludes" in dir(config):
      for rexclude in config.rexcludes[config.wpaths.index(wpath)]:
        args.append("--exclude=%s" % rexclude)
    args.append(wpath)
    rpath = config.rpaths[config.wpaths.index(wpath)]
    args.append("%s")
    _cmd = " ".join(args)
    for node in config.rnodes:
      cmd = (_cmd % (node + rpath))
      if self.pretend:
        syslog("would execute `%s'" % cmd)
      if self.sched:
        syslog("sched execute `%s'" % cmd)
        LAST_EVENT[cmd] = time.time()
      else:
        call(cmd)

  def process_default(self, event):
    syslog(LOG_DEBUG, "caught %s on %s" % \
        (event.maskname, os.path.join(event.path, event.name)))
    for wpath in config.wpaths:
      if os.path.realpath(wpath) + '/' in os.path.realpath(event.path) + '/':
        for rexclude in config.rexcludes[config.wpaths.index(wpath)]:
          if os.path.realpath(os.path.join(wpath, rexclude)) + '/' in os.path.realpath(os.path.join(event.path, event.name)) + '/':
            syslog(LOG_DEBUG, "ignore event because '%s' in rexcludes " % \
                   (rexclude))
            return
        self.sync(wpath)
        return

def daemonize():
  try:
    pid = os.fork()
  except OSError, e:
    raise Exception, "%s [%d]" % (e.strerror, e.errno)

  if (pid == 0):
    os.setsid()
    try:
      pid = os.fork()
    except OSError, e:
      raise Exception, "%s [%d]" % (e.strerror, e.errno)
    if (pid == 0):
      os.chdir('/')
      os.umask(0)
    else:
      os._exit(0)
  else:
    os._exit(0)

  os.open("/dev/null", os.O_RDWR)
  os.dup2(0, 1)
  os.dup2(0, 2)

  return 0

def load_config(filename):
  if not os.path.isfile(filename):
    raise RuntimeError, "Configuration file does not exist: %s" % filename

  configdir  = os.path.dirname(filename)
  configfile = os.path.basename(filename)

  if configfile.endswith(".py"):
    configfile = configfile[0:-3]
  else:
    raise RuntimeError, "Configuration file must be a importable python file ending in .py"

  sys.path.append(configdir)
  exec("import %s as __config__" % configfile)
  sys.path.remove(configdir)

  global config
  config = __config__

  if not "wpaths" in dir(config):
    raise RuntimeError, "no paths given to watch"
  for wpath in config.wpaths:
    if not os.path.isdir(wpath):
      raise RuntimeError, "one of the watch paths does not exist: %s" % wpath
    if not os.path.isabs(wpath):
      config.wpaths[config.wpaths.index(wpath)] = os.path.abspath(wpath)
  
  for owpath in config.wpaths:
    for wpath in config.wpaths:
      if os.path.realpath(owpath) in os.path.realpath(wpath) and wpath != owpath and len(os.path.split(wpath)) <> len(os.path.split(owpath)):
	raise RuntimeError, "You cannot specify %s in wpaths which is a subdirectory of %s since it is already synced." % (wpath, owpath)


  if not "rpaths" in dir(config):
    raise RuntimeError, "no paths given for the transfer"
  if len(config.wpaths) != len(config.rpaths):
    raise RuntimeError, "the no. of remote paths must be equal to the number of watched paths"
  


  if not "rnodes" in dir(config) or len(config.rnodes) < 1:
    raise RuntimeError, "no remote nodes given"

  if not "rspeed" in dir(config) or config.rspeed < 0:
    config.rspeed = 0

  if not "emask" in dir(config):
    config.emask = DEFAULT_EVENTS
  for event in config.emask:
    if not event in EventsCodes.ALL_FLAGS.keys():
      raise RuntimeError, "invalid inotify event: %s" % event

  if not "edelay" in dir(config):
    config.edelay = 10
  if config.edelay < 0:
    raise RuntimeError, "event delay needs to be greater or equal to 0"

  if not "logfile" in dir(config):
    config.logfile = None

  if not "extra" in dir(config):
    config.extra = ""
  if not "rsync" in dir(config):
    config.rsync = "/usr/bin/rsync"
  if not os.path.isabs(config.rsync):
    raise RuntimeError, "rsync path needs to be absolute"
  if not os.path.isfile(config.rsync):
    raise RuntimeError, "rsync binary does not exist: %s" % config.rsync

def main():
  version = ".".join(map(str, __version__))
  parser = OptionParser(option_list=OPTION_LIST,version="%prog " + version)
  (options, args) = parser.parse_args()

  if len(args) > 0:
    parser.error("too many arguments")

  logopt = LOG_PID|LOG_CONS
  if not options.daemonize:
    logopt |= LOG_PERROR
  openlog("inosync", logopt, LOG_DAEMON)
  if options.verbose:
    setlogmask(LOG_UPTO(LOG_DEBUG))
  else:
    setlogmask(LOG_UPTO(LOG_INFO))

  load_config(options.config)

  if options.daemonize:
    daemonize()

  wm = WatchManager()
  ev = RsyncEvent(options.pretend, options.sched)
  notifier = AsyncNotifier(wm, ev, read_freq=config.edelay)
  mask = reduce(lambda x,y: x|y, [EventsCodes.ALL_FLAGS[e] for e in config.emask])
  wds = wm.add_watch(config.wpaths, mask, rec=True, auto_add=True)
  for wpath in config.wpaths:
    syslog(LOG_DEBUG, "starting initial synchronization on %s" % wpath)
    ev.sync(wpath)
    syslog(LOG_DEBUG, "initial synchronization on %s done" % wpath)
    syslog("resuming normal operations on %s" % wpath)
  while True:
    asyncore.loop(1, count=1)
    sched()
  sys.exit(0)

if __name__ == "__main__":
  main()
