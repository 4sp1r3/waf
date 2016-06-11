#!/usr/bin/env python
# encoding: utf-8
# Carlos Rafael Giani, 2006
# Thomas Nagy, 2010

"""
Unit testing system for C/C++/D providing test execution:

* in parallel, by using ``waf -j``
* partial (only the tests that have changed) or full (by using ``waf --alltests``)

The tests are declared by adding the **test** feature to programs::

	def options(opt):
		opt.load('compiler_cxx waf_unit_test')
	def configure(conf):
		conf.load('compiler_cxx waf_unit_test')
	def build(bld):
		bld(features='cxx cxxprogram test', source='main.cpp', target='app')
		# or
		bld.program(features='test', source='main2.cpp', target='app2')

When the build is executed, the program 'test' will be built and executed without arguments.
The success/failure is detected by looking at the return code. The status and the standard output/error
are stored on the build context.

The results can be displayed by registering a callback function. Here is how to call
the predefined callback::

	def build(bld):
		bld(features='cxx cxxprogram test', source='main.c', target='app')
		from waflib.Tools import waf_unit_test
		bld.add_post_fun(waf_unit_test.summary)
"""

import os
from waflib.TaskGen import feature, after_method, taskgen_method
from waflib import Utils, Task, Logs, Options
testlock = Utils.threading.Lock()

@feature('test')
@after_method('apply_link')
def make_test(self):
	"""Create the unit test task. There can be only one unit test task by task generator."""
	if getattr(self, 'link_task', None):
		self.create_task('utest', self.link_task.outputs)


@taskgen_method
def add_test_results(self, tup):
	"""Override and return tup[1] to interrupt the build immediately if a test does not run"""
	Logs.debug("ut: %r", tup)
	self.utest_result = tup
	try:
		self.bld.utest_results.append(tup)
	except AttributeError:
		self.bld.utest_results = [tup]

class utest(Task.Task):
	"""
	Execute a unit test
	"""
	color = 'PINK'
	after = ['vnum', 'inst']
	vars = []
	def runnable_status(self):
		"""
		Always execute the task if `waf --alltests` was used or no
		tests if ``waf --notests`` was used
		"""
		if getattr(Options.options, 'no_tests', False):
			return Task.SKIP_ME

		ret = super(utest, self).runnable_status()
		if ret == Task.SKIP_ME:
			if getattr(Options.options, 'all_tests', False):
				return Task.RUN_ME
		return ret

	def add_path(self, dct, path, var):
		dct[var] = os.pathsep.join(Utils.to_list(path) + [os.environ.get(var, '')])

	def get_test_env(self):
		"""
		In general, tests may require any library built anywhere in the project.
		Override this method if fewer paths are needed
		"""
		try:
			fu = getattr(self.generator.bld, 'all_test_paths')
		except AttributeError:
			# this operation may be performed by at most #maxjobs
			fu = os.environ.copy()

			lst = []
			for g in self.generator.bld.groups:
				for tg in g:
					if getattr(tg, 'link_task', None):
						s = tg.link_task.outputs[0].parent.abspath()
						if s not in lst:
							lst.append(s)

			if Utils.is_win32:
				self.add_path(fu, lst, 'PATH')
			elif Utils.unversioned_sys_platform() == 'darwin':
				self.add_path(fu, lst, 'DYLD_LIBRARY_PATH')
				self.add_path(fu, lst, 'LD_LIBRARY_PATH')
			else:
				self.add_path(fu, lst, 'LD_LIBRARY_PATH')
			self.generator.bld.all_test_paths = fu
		return fu

	def run(self):
		"""
		Execute the test. The execution is always successful, and the results
		are stored on ``self.generator.bld.utest_results`` for postprocessing.

		Override ``add_test_results`` to interrupt the build
		"""

		filename = self.inputs[0].abspath()
		self.ut_exec = getattr(self.generator, 'ut_exec', [filename])
		if getattr(self.generator, 'ut_fun', None):
			self.generator.ut_fun(self)


		cwd = getattr(self.generator, 'ut_cwd', '') or self.inputs[0].parent.abspath()

		testcmd = getattr(self.generator, 'ut_cmd', False) or getattr(Options.options, 'testcmd', False)
		if testcmd:
			self.ut_exec = (testcmd % " ".join(self.ut_exec)).split(' ')

		proc = Utils.subprocess.Popen(self.ut_exec, cwd=cwd, env=self.get_test_env(), stderr=Utils.subprocess.PIPE, stdout=Utils.subprocess.PIPE)
		(stdout, stderr) = proc.communicate()

		self.waf_unit_test_results = tup = (filename, proc.returncode, stdout, stderr)
		testlock.acquire()
		try:
			return self.generator.add_test_results(tup)
		finally:
			testlock.release()

	def post_run(self):
		super(utest, self).post_run()
		if getattr(Options.options, 'clear_failed_tests', False) and self.waf_unit_test_results[1]:
			self.generator.bld.task_sigs[self.uid()] = None

def summary(bld):
	"""
	Display an execution summary::

		def build(bld):
			bld(features='cxx cxxprogram test', source='main.c', target='app')
			from waflib.Tools import waf_unit_test
			bld.add_post_fun(waf_unit_test.summary)
	"""
	lst = getattr(bld, 'utest_results', [])
	if lst:
		Logs.pprint('CYAN', 'execution summary')

		total = len(lst)
		tfail = len([x for x in lst if x[1]])

		Logs.pprint('CYAN', '  tests that pass %d/%d' % (total-tfail, total))
		for (f, code, out, err) in lst:
			if not code:
				Logs.pprint('CYAN', '    %s' % f)

		Logs.pprint('CYAN', '  tests that fail %d/%d' % (tfail, total))
		for (f, code, out, err) in lst:
			if code:
				Logs.pprint('CYAN', '    %s' % f)

def set_exit_code(bld):
	"""
	If any of the tests fail waf will exit with that exit code.
	This is useful if you have an automated build system which need
	to report on errors from the tests.
	You may use it like this:

		def build(bld):
			bld(features='cxx cxxprogram test', source='main.c', target='app')
			from waflib.Tools import waf_unit_test
			bld.add_post_fun(waf_unit_test.set_exit_code)
	"""
	lst = getattr(bld, 'utest_results', [])
	for (f, code, out, err) in lst:
		if code:
			msg = []
			if out:
				msg.append('stdout:%s%s' % (os.linesep, out.decode('utf-8')))
			if err:
				msg.append('stderr:%s%s' % (os.linesep, err.decode('utf-8')))
			bld.fatal(os.linesep.join(msg))


def options(opt):
	"""
	Provide the ``--alltests``, ``--notests`` and ``--testcmd`` command-line options.
	"""
	opt.add_option('--notests', action='store_true', default=False, help='Exec no unit tests', dest='no_tests')
	opt.add_option('--alltests', action='store_true', default=False, help='Exec all unit tests', dest='all_tests')
	opt.add_option('--clear-failed', action='store_true', default=False, help='Force failed unit tests to run again next time', dest='clear_failed_tests')
	opt.add_option('--testcmd', action='store', default=False,
	 help = 'Run the unit tests using the test-cmd string'
	 ' example "--test-cmd="valgrind --error-exitcode=1'
	 ' %s" to run under valgrind', dest='testcmd')

