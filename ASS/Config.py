import logging
import os
import sys


class Config:
    def __init__(self, project_name=''):
        self._set_ass_tag_prefix()
        self._init_logger(project_name)
        self.aws_authenticated = False

    def get_logger(self):
        return self.logger

    def get_ass_tag_prefix(self):
        return self.ass_tag_prefix

    def get_state_bucket_name(self, region, account_id):
        return "{}-{}-stop-start-state-bucket".format(region, account_id)

    def aws_authenticated(self):
        return self.aws_authenticated

    def full_ass_tag(self, tag):
        return "{}{}".format(self.get_ass_tag_prefix(), tag)

    def _set_ass_tag_prefix(self):
        if 'ASS_TAG_PREFIX' in os.environ:
            self.ass_tag_prefix = "{}:".format(os.environ['ASS_TAG_PREFIX'])
        else:
            self.ass_tag_prefix = ""

    def _init_logger(self, project_name):
        self.logger = logging.getLogger(project_name)
        formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
        ch = logging.StreamHandler(sys.stdout)

        if 'DEBUG' in os.environ and os.environ['DEBUG'] == '1':
            self.logger.setLevel(logging.DEBUG)
            ch.setLevel(logging.DEBUG)
        else:
            self.logger.setLevel(logging.INFO)
            ch.setLevel(logging.INFO)

        ch.setFormatter(formatter)
        self.logger.addHandler(ch)
