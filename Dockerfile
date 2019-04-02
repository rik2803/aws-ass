FROM python:3.7-alpine3.7

ADD requirements.txt /requirements.txt
ADD ass-stop.py /aws-delete-tagged-cfn-stacks.py
ADD ASS /ASS

RUN pip install -r /requirements.txt

ENTRYPOINT ["python3", "/ass-stop.py"]