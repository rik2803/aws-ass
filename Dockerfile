FROM python:3.7-alpine3.7

ADD requirements.txt /requirements.txt
ADD aws-delete-tagged-cfn-stacks.py /aws-delete-tagged-cfn-stacks.py

RUN pip install -r /requirements.txt

ENTRYPOINT ["python3", "/aws-delete-tagged-cfn-stacks.py"]