import requests
from urllib.parse import urlparse, parse_qs
from os import path
from http.server import BaseHTTPRequestHandler, HTTPServer
from configparser import ConfigParser
import click

config = ConfigParser()
config.read("creds.conf")

client_id = config["SPOTIFY"]["client_id"]
client_secret = config["SPOTIFY"]["client_secret"]
redirect_uri = config["SPOTIFY"]["redirect_url"]
redirect_port = config["SPOTIFY"].getint("redirect_port")

redirect_url = f"{redirect_uri}:{redirect_port}"


class AuthRequestHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        html_body = """
            <html> 
            <head>
                <title> Spotify OAuth </title> 
            </head>
            <body>
            <h1>Logged In please close this page!.</h1>
            </body>
            </html>
        """.strip()
        self.send_response(200, "OK")
        self.send_header("Content-type", "html")
        self.end_headers()

        self.wfile.write(bytes(html_body, "UTF-8"))
        auth_code_path = self.path

        parsed_url = urlparse(auth_code_path)
        code = parse_qs(parsed_url.query)["code"]

        token_url = "https://accounts.spotify.com/api/token"
        body = {
            "code": code,
            "redirect_uri": redirect_url,
            "grant_type": "authorization_code",
        }

        response = requests.post(
            token_url, data=body, auth=(client_id, client_secret)
        ).json()

        refresh_token = response["refresh_token"]

        open(".refresh_token", "w").write(refresh_token)
        print("[*] Stored Refresh Token.")


def get_refresh_token(server_class=HTTPServer, handler_class=AuthRequestHandler):
    server_address = ("", redirect_port)
    httpd = server_class(server_address, handler_class)
    url = f"https://accounts.spotify.com/authorize?response_type=code&client_id={client_id}&redirect_uri={redirect_url}"
    click.launch(url)
    httpd.handle_request()


class SpotifyCharts:
    def __init__(self) -> None:
        if not path.exists(".refresh_token"):
            print("[!] Refresh Token Not Found. Login To Spotify!")
            get_refresh_token()

        else:
            refresh_token = open(".refresh_token", "r").read()

            refresh_token_url = "https://accounts.spotify.com/api/token"
            data = {"grant_type": "refresh_token", "refresh_token": refresh_token}

            res = requests.post(
                refresh_token_url, auth=(client_id, client_secret), data=data
            ).json()

            access_token = res["access_token"]
            self.headers = {"Authorization": f"Bearer {access_token}"}

    def get_countries(self) -> list:
        """
        :returns -> Dict list of country code and its country name
        """
        countries = [
            {"countryCode": "GLOBAL", "countryName": "Global"},
            {"countryCode": "IN", "countryName": "India"},
            {"countryCode": "AR", "countryName": "Argentina"},
            {"countryCode": "AU", "countryName": "Australia"},
            {"countryCode": "AT", "countryName": "Austria"},
            {"countryCode": "BY", "countryName": "Belarus"},
            {"countryCode": "BE", "countryName": "Belgium"},
            {"countryCode": "BO", "countryName": "Bolivia"},
            {"countryCode": "BR", "countryName": "Brazil"},
            {"countryCode": "BG", "countryName": "Bulgaria"},
            {"countryCode": "CA", "countryName": "Canada"},
            {"countryCode": "CL", "countryName": "Chile"},
            {"countryCode": "CO", "countryName": "Colombia"},
            {"countryCode": "CR", "countryName": "Costa Rica"},
            {"countryCode": "CZ", "countryName": "Czech Republic"},
            {"countryCode": "DK", "countryName": "Denmark"},
            {"countryCode": "DO", "countryName": "Dominican Republic"},
            {"countryCode": "EC", "countryName": "Ecuador"},
            {"countryCode": "EG", "countryName": "Egypt"},
            {"countryCode": "SV", "countryName": "El Salvador"},
            {"countryCode": "EE", "countryName": "Estonia"},
            {"countryCode": "FI", "countryName": "Finland"},
            {"countryCode": "FR", "countryName": "France"},
            {"countryCode": "DE", "countryName": "Germany"},
            {"countryCode": "GR", "countryName": "Greece"},
            {"countryCode": "GT", "countryName": "Guatemala"},
            {"countryCode": "HN", "countryName": "Honduras"},
            {"countryCode": "HK", "countryName": "Hong Kong"},
            {"countryCode": "HU", "countryName": "Hungary"},
            {"countryCode": "IS", "countryName": "Iceland"},
            {"countryCode": "ID", "countryName": "Indonesia"},
            {"countryCode": "IE", "countryName": "Ireland"},
            {"countryCode": "IL", "countryName": "Israel"},
            {"countryCode": "JP", "countryName": "Japan"},
            {"countryCode": "KZ", "countryName": "Kazakhstan"},
            {"countryCode": "LV", "countryName": "Latvia"},
            {"countryCode": "LT", "countryName": "Lithuania"},
            {"countryCode": "LU", "countryName": "Luxembourg"},
            {"countryCode": "MY", "countryName": "Malaysia"},
            {"countryCode": "MX", "countryName": "Mexico"},
            {"countryCode": "MA", "countryName": "Morocco"},
            {"countryCode": "NL", "countryName": "Netherlands"},
            {"countryCode": "NZ", "countryName": "New Zealand"},
            {"countryCode": "NI", "countryName": "Nicaragua"},
            {"countryCode": "NG", "countryName": "Nigeria"},
            {"countryCode": "NO", "countryName": "Norway"},
            {"countryCode": "PK", "countryName": "Pakistan"},
            {"countryCode": "PA", "countryName": "Panama"},
            {"countryCode": "PY", "countryName": "Paraguay"},
            {"countryCode": "PE", "countryName": "Peru"},
            {"countryCode": "PH", "countryName": "Philippines"},
            {"countryCode": "PL", "countryName": "Poland"},
            {"countryCode": "PT", "countryName": "Portugal"},
            {"countryCode": "RO", "countryName": "Romania"},
            {"countryCode": "SA", "countryName": "Saudi Arabia"},
            {"countryCode": "SG", "countryName": "Singapore"},
            {"countryCode": "SK", "countryName": "Slovakia"},
            {"countryCode": "ZA", "countryName": "South Africa"},
            {"countryCode": "KR", "countryName": "South Korea"},
            {"countryCode": "ES", "countryName": "Spain"},
            {"countryCode": "SE", "countryName": "Sweden"},
            {"countryCode": "CH", "countryName": "Switzerland"},
            {"countryCode": "TW", "countryName": "Taiwan"},
            {"countryCode": "TH", "countryName": "Thailand"},
            {"countryCode": "TR", "countryName": "Turkey"},
            {"countryCode": "AE", "countryName": "UAE"},
            {"countryCode": "UA", "countryName": "Ukraine"},
            {"countryCode": "GB", "countryName": "United Kingdom"},
            {"countryCode": "UY", "countryName": "Uruguay"},
            {"countryCode": "US", "countryName": "USA"},
            {"countryCode": "VE", "countryName": "Venezuela"},
            {"countryCode": "VN", "countryName": "Vietnam"},
        ]

        return countries

    def daily_top_songs(self, country_code: str, date_: str = "latest") -> list:
        """
        :param country_code: Country code of the country eg. IN -> India
        :param date_: To get data for a specific date, By default its Latest. eg. 2022-12-24

        :return: a list of daily top 200 songs from spotify charts.
        """
        url = f"https://charts-spotify-com-service.spotify.com/auth/v0/charts/regional-{country_code.lower()}-daily/{date_}"

        response = requests.get(url, headers=self.headers)
        response = response.json()
        return response

    def daily_top_artists(self, country_code: str, date_: str = "latest") -> list:
        """
        :param country_code: Country code of the country eg. IN -> India
        :param date_: To get data for a specific date, By default its Latest. eg. 2022-12-24

        :return: a list of daily top 200 artists from spotify charts.
        """
        url = f"https://charts-spotify-com-service.spotify.com/auth/v0/charts/artist-{country_code.lower()}-daily/{date_}"

        response = requests.get(url, headers=self.headers)
        response = response.json()
        return response

    def daily_viral_songs(self, country_code: str, date_: str = "latest") -> list:
        """
        :param country_code: Country code of the country eg. IN -> India
        :param date_: To get data for a specific date, By default its Latest. eg. 2022-12-24

        :return: a list of daily viral 200 songs from spotify charts.
        """

        url = f"https://charts-spotify-com-service.spotify.com/auth/v0/charts/viral-{country_code.lower()}-daily/{date_}"

        response = requests.get(url, headers=self.headers)
        response = response.json()
        return response

    def weekly_top_songs(self, country_code: str, date_="latest") -> list:
        """
        :param country_code: Country code of the country eg. IN -> India
        :param date_: Week Start Date.

        :return: a list of weekly top 200 songs from spotify charts.
        """
        url = f"https://charts-spotify-com-service.spotify.com/auth/v0/charts/regional-{country_code.lower()}-weekly/{date_}"

        response = requests.get(url, headers=self.headers)
        response = response.json()
        return response

    def weekly_top_artists(self, country_code: str, date_="latest") -> list:
        """
        :param country_code: Country code of the country eg. IN -> India
        :param date_: Week Start Date.

        :return: a list of daily top 200 artists from spotify charts.
        """

        url = f"https://charts-spotify-com-service.spotify.com/auth/v0/charts/artist-{country_code.lower()}-weekly/{date_}"

        response = requests.get(url, headers=self.headers)
        response = response.json()
        return response

    def weekly_top_albums(self, country_code: str, date_="latest") -> list:
        """
        :param country_code: Country code of the country eg. IN -> India
        :param date_: To get data for a specific date, By default its Latest. eg. 2022-12-24

        :return: a list of weekly top 200 albums from spotify charts.
        """

        url = f"https://charts-spotify-com-service.spotify.com/auth/v0/charts/album-{country_code.lower()}-daily/{date_}"

        response = requests.get(url, headers=self.headers)
        response = response.json()
        return response
