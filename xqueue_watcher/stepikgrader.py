"""
Implementation of a grader compatible with XServer hosted at Stepik.org
"""
import os
import sys
import six
import time
import traceback
import json
import pprint
from path import path
import logging
import multiprocessing
# from statsd import statsd
import contextlib
import importlib.util
import smtplib, ssl
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

import epicbox


def load_module(name, module_file):
    spec = importlib.util.spec_from_file_location(name, os.path.abspath(os.path.expanduser(module_file)))
    foo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(foo)
    return foo


def unwrap(x):
    return x[0] if isinstance(x, tuple) else x


def check_mail(params, logger):
    # TODO: consider STARTTLS and other protocols
    try:
        context = ssl.create_default_context()
        with smtplib.SMTP_SSL(params["server"], params["port"], context=context) as server:
            server.login(params["email"], params["password"])
            server.quit()
        assert (isinstance(params["recipients"], list))
        logger.debug("alert mail server configured")
        return True
    except Exception as e:
        logger.exception("check_mail")
        return False


class StepikGrader(object):

    TECH_DIFF_MSG = "Упс.\nВозникла проблема на нашей стороне, над которой, скорей всего, мы уже " \
                    "работаем.\nСообщите номер вашего решения по адресу, указанному в курсе, и " \
                    "воздержитесь от дальнейшей отправки решений до объявления."

    def __init__(self, grader_root='/tmp/', fork_per_item=True, logger_name=__name__,
                 fail_on_error=False, alert_mail=None):
        """
        grader_root = root path to graders
        fork_per_item = fork a process for every request
        logger_name = name of logger
        alert_mail = SMTP credentials and recipients list
        """
        self.log = logging.getLogger(logger_name)
        self.grader_root = path(grader_root)

        self.fork_per_item = fork_per_item
        self.fail_on_error = fail_on_error
        self.alert_mail = alert_mail if check_mail(alert_mail, self.log) else None

        epicbox.configure(profiles=[epicbox.Profile('python', 'python:3.7-alpine')])

    def send_alert(self, type, path, body, error_txt):
        try:
            own_address = self.alert_mail["email"]

            subject = f"{type} in {path if path is not None else 'processing system'}"
            if body is not None:
                payload = pprint.pformat(body.get("grader_payload", None), indent=2)
                response = body.get("student_response", "<none?!>")
            else:
                payload, response = None, None

            message = MIMEMultipart("alternative")
            message["Subject"] = subject
            message["From"] = own_address

            text = f"Subject: {subject}\n\n" \
                f"{error_txt}\n\n" \
                f"Payload:\n{payload}\n\n" \
                f"Submission:\n<<< START OF TEXT >>>\n" \
                f"{response}\n" \
                f"<<<END OF TEXT>>>\n"

            message.attach(MIMEText(text, "plain"))

            context = ssl.create_default_context()
            with smtplib.SMTP_SSL(self.alert_mail["server"], self.alert_mail["port"], context=context) as server:
                server.login(own_address, self.alert_mail["password"])
                for address in self.alert_mail["recipients"]:
                    message["To"] = address
                    server.sendmail(own_address, address, message.as_string())
                server.quit()
        except Exception as e:
            self.log.exception("send_alert")

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
        grader_path, body = None, None
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
            self.log.exception("process_item")
            if self.alert_mail:
                self.send_alert(type(e).__name__, grader_path, body,
                                traceback.format_exc())
            if self.fail_on_error:
                reply = {'score': 0, 'msg': self.TECH_DIFF_MSG}
                if queue:
                    queue.put(reply)
                return reply
            elif queue:
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
            if isinstance(test_data, (str, tuple)):
                test_data = [test_data]
                suite_size = grader_config.get("SUITE_SIZE", 20)
                for i in range(suite_size-1):
                    test_data.append(grader.generate())
            elif isinstance(test_data, list):
                pass  # all right!
            else:
                raise AssertionError(f"{grader_path}: generate() must return a list or at least a single test!")

            files = [{'name': 'main.py', 'content': student_response.encode()}]
            rates = []

            with epicbox.create('python', 'python3 main.py', files=files, limits=limits) as sandbox:

                for test in test_data:
                    if isinstance(test, tuple):
                        if not len(test) == 2:
                            raise AssertionError(f"{grader_path}: bad clued test!")
                        stdin_text, clue = test
                        must_solve = False
                    elif isinstance(test, str):
                        stdin_text = test
                        # TODO: fork it to save time?
                        clue = None
                        must_solve = True
                    else:
                        raise AssertionError(f"{grader_path}: test type isn't str!")

                    result = epicbox.start(sandbox, stdin=stdin_text)

                    # TODO: опция для игнорирования некорректных результатов
                    if result['timeout']:
                        return {'score': 0, 'msg': 'Превышено время выполнения решения'}
                    if result['oom_killed']:
                        return {'score': 0, 'msg': 'Решение не уложилось в отведённые ресурсы'}
                    if result['exit_code'] != 0:
                        return {'score': 0, 'msg': 'Произошла ошибка при выполнении'}

                    stdout_raw = result['stdout']
                    if hasattr(grader, "post_process"):
                        stdout_text = grader.post_process(stdout_raw)
                    else:
                        stdout_text = stdout_raw.decode("utf-8").strip()

                    if must_solve:
                        clue = grader.solve(test)

                    rate = grader.check(stdout_text, clue)

                    if isinstance(rate, tuple):
                        if not len(test) == 2:
                            raise AssertionError(f"{grader_path}: bad commented check rate!")
                        if rate[0] is None:
                            return {'score': 0, 'msg': rate[1]}
                    elif rate is None:
                        return {'score': 0, 'msg': ''}

                    rates.append(rate)

            if hasattr(grader, "evaluate"):
                final_rate = grader.evaluate(rates)
            else:
                final_rate = sum(map(unwrap, rates))/len(rates)

            if isinstance(final_rate, tuple):
                return {'score': final_rate[0], 'msg': final_rate[1]}
            else:
                return {'score': final_rate, 'msg': self.default_msg(final_rate)}
        except Exception as e:
            self.log.exception("grade")
            raise
