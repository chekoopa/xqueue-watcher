"""
Implementation of a grader compatible with XServer hosted at Stepik.org
"""
import os
import sys
import six
import time
import json
from path import path
import logging
import multiprocessing
# from statsd import statsd
import contextlib
import importlib.util

import epicbox


def load_module(name, module_file):
    spec = importlib.util.spec_from_file_location(name, os.path.abspath(os.path.expanduser(module_file)))
    foo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(foo)
    return foo


class StepikGrader(object):

    TECH_DIFF_MSG = "Упс.\nВозникла проблема на нашей стороне, над которой, скорей всего, мы уже " \
                    "работаем.\nСообщите номер вашего решения по адресу, указанному в курсе, и " \
                    "воздержитесь от дальнейшей отправки решений до объявления."

    def __init__(self, grader_root='/tmp/', fork_per_item=True, logger_name=__name__):
        """
        grader_root = root path to graders
        fork_per_item = fork a process for every request
        logger_name = name of logger
        """
        self.log = logging.getLogger(logger_name)
        self.grader_root = path(grader_root)

        self.fork_per_item = fork_per_item

        epicbox.configure(profiles=[epicbox.Profile('python', 'python:3.7-alpine')])

    def __call__(self, content):
        if self.fork_per_item:
            q = multiprocessing.Queue()
            proc = multiprocessing.Process(target=self.process_item, args=(content, q))
            proc.start()
            proc.join()
            reply = q.get_nowait()
            if isinstance(reply, Exception):
                raise reply
            else:
                return reply
        else:
            return self.process_item(content)

    def process_item(self, content, queue=None):
        try:
            # statsd.increment('xqueuewatcher.process-item')
            body = content['xqueue_body']
            # files = content.get('xqueue_files', {})
            # {"<FILENAME>": "https://stepik.org/media/submissions/.../foobar.py"}

            body = json.loads(body)
            student_response = body['student_response']
            payload = body['grader_payload']
            try:
                grader_config = json.loads(payload)
            except ValueError as err:
                # statsd.increment('xqueuewatcher.grader_payload_error')
                self.log.debug("error parsing: '{0}' -- {1}".format(payload, err))
                raise

            self.log.debug("Processing submission, grader payload: {0}".format(payload))

            relative_grader_path = grader_config['grader']
            grader_path = (self.grader_root / relative_grader_path).abspath()

            # start = time.time()
            results = self.grade(grader_path, grader_config, student_response)

            # statsd.histogram('xqueuewatcher.grading-time', time.time() - start)

            # Make valid JSON message
            reply = {
                'score': results['score'],
                'msg': results['msg']
            }

            # statsd.increment('xqueuewatcher.replies (non-exception)')

        except Exception as e:
            # TODO: REPORT THE PROBLEM
            self.log.exception("process_item")
            if queue:
                queue.put(e)
            else:
                raise
        else:
            if queue:
                queue.put(reply)
            return reply

    def default_msg(self, score):
        if score == 0:
            return "Something is incorrect, try again!"
        elif score < 0.75:
            return "Not bad, but you can do better!"
        else:
            return "Good job!"

    def grade(self, grader_path, grader_config, student_response):
        try:
            grader = load_module("grader", grader_path)
            limits = {  # default at the moment
                # CPU time in seconds, None for unlimited
                'cputime': 1,
                # Real time in seconds, None for unlimited
                'realtime': 5,
                # Memory in megabytes, None for unlimited
                'memory': 64,
                # limit the max processes the sandbox can have
                # -1 or None for unlimited(default)
                'processes': -1,
            }

            test_data = grader.generate()
            if not isinstance(test_data, list):
                raise AssertionError(f"{grader_path}: generate() must return a list!")

            files = [{'name': 'main.py', 'content': student_response.encode()}]
            rates = []

            with epicbox.create('python', 'python3 main.py', files=files, limits=limits) as sandbox:

                for test in test_data:
                    if isinstance(test, tuple):
                        if not len(test) == 2:
                            raise AssertionError(f"{grader_path}: bad clued test!")
                        stdin_text, clue = test
                        must_solve = False
                    else:
                        stdin_text = test
                        # TODO: fork it to save time?
                        clue = None
                        must_solve = True

                    result = epicbox.start(sandbox, stdin=stdin_text)

                    if result['timeout']:
                        return {'score': 0, 'msg': 'Timeout'}
                    if result['oom_killed']:
                        return {'score': 0, 'msg': 'Resource error'}
                    if result['exit_code'] != 0:
                        return {'score': 0, 'msg': 'Runtime error'}

                    stdout_raw = result['stdout']
                    if hasattr(grader, "post_process"):
                        stdout_text = grader.post_process(stdout_raw)
                    else:
                        stdout_text = stdout_raw.decode("utf-8").strip()

                    if must_solve:
                        clue = grader.solve(test)

                    rates.append(grader.check(stdout_text, clue))

            final_rate = grader.evaluate(rates)

            if isinstance(final_rate, tuple):
                return {'score': final_rate[0], 'msg': final_rate[1]}
            else:
                return {'score': final_rate, 'msg': self.default_msg(final_rate)}
        except Exception as e:
            self.log.exception("grade")
            return {'score': 0, 'msg': self.TECH_DIFF_MSG}