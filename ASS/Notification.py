import os
from httplib2 import Http
from jira import JIRA
from json import dumps


class Notification:

    @staticmethod
    def post_message_to_google_chat(summary: str, description: str):
        message_headers = {'Content-Type': 'application/json; charset=UTF-8'}
        chat_url = os.getenv('CHATURL')

        http_obj = Http()

        text = f"{summary} \n{description}"

        return http_obj.request(
            uri=chat_url,
            method='POST',
            headers=message_headers,
            body=dumps({'text': text})
        )

    @staticmethod
    def create_jira_ticket(summary: str, description: str):

        jira_url = os.getenv('JIRA_URL')
        jira_user = os.getenv('JIRA_USER')
        jira_password = os.getenv('JIRA_API_PASSWORD')

        options = {'server': jira_url}
        jira = JIRA(options, basic_auth=(jira_user, jira_password))

        issue_dict = {
            'project': {'id': 16937},
            'summary': f'{summary}',
            'description': f'{description}',
            'issuetype': {'name': 'Support'},
        }

        jira.create_issue(fields=issue_dict)

    @staticmethod
    def send_notification(summary: str, description: str = ""):

        notificationmode = os.getenv('NOTIFICATION_MODE')

        if notificationmode == 'GOOGLECHAT':
            Notification.post_message_to_google_chat(summary, description)
        elif notificationmode == 'JIRA':
            Notification.create_jira_ticket(summary, description)
