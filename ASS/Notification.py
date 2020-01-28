import os
from httplib2 import Http
from json import dumps


class Notification:

    # Default value is the 1ste Lijn Support Google Chat channel
    default_chat_url = "https://chat.googleapis.com/v1/spaces/AAAA7-V1xdw/messages?key=AIzaSyDdI0hCZtE6vySjMm-WEfRq3CPzqKqqsHI&token=Aju_BkmuyN6EMf7Y1x8yTpzvzT2QvJlckCP4Bpz8LIA%3D"
    chat_url = os.getenv("CHATURL", default_chat_url)

    if chat_url == "":
        chat_url = default_chat_url

    @staticmethod
    def post_message_to_google_chat(text: str):
        message_headers = {'Content-Type': 'application/json; charset=UTF-8'}

        http_obj = Http()

        return http_obj.request(
            uri=Notification.chat_url,
            method='POST',
            headers=message_headers,
            body=dumps({'text': text})
        )
