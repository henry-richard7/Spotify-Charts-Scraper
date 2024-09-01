from httpx import AsyncClient, Client
from urllib.parse import urlparse, parse_qs
from os import path
from http.server import BaseHTTPRequestHandler, HTTPServer
import click
from Countries import Countries
from Settings import SpotifySettings

spotify_settings = SpotifySettings()

client_id = spotify_settings.client_id
client_secret = spotify_settings.client_secret
redirect_uri = spotify_settings.redirect_url
redirect_port = spotify_settings.redirect_port

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

        response = (
            Client().post(token_url, data=body, auth=(client_id, client_secret)).json()
        )

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

        refresh_token = open(".refresh_token", "r").read()

        refresh_token_url = "https://accounts.spotify.com/api/token"
        data = {"grant_type": "refresh_token", "refresh_token": refresh_token}

        res = (
            Client()
            .post(refresh_token_url, auth=(client_id, client_secret), data=data)
            .json()
        )

        access_token = res["access_token"]
        self.headers = {"Authorization": f"Bearer {access_token}"}

    def get_countries(self) -> Countries:
        """
        :returns -> Enum Countries
        """

        return Countries

    async def daily_top_songs(self, country_code: str, date_: str = "latest") -> list:
        """
        :param country_code: Country code of the country eg. IN -> India
        :param date_: To get data for a specific date, By default its Latest. eg. 2022-12-24

        :return: a list of daily top 200 songs from spotify charts.
        """
        url = f"https://charts-spotify-com-service.spotify.com/auth/v0/charts/regional-{country_code.lower()}-daily/{date_}"

        async with AsyncClient() as client:
            response = await client.get(url, headers=self.headers)
        response = response.json()
        return response

    async def daily_top_artists(self, country_code: str, date_: str = "latest") -> list:
        """
        :param country_code: Country code of the country eg. IN -> India
        :param date_: To get data for a specific date, By default its Latest. eg. 2022-12-24

        :return: a list of daily top 200 artists from spotify charts.
        """
        url = f"https://charts-spotify-com-service.spotify.com/auth/v0/charts/artist-{country_code.lower()}-daily/{date_}"

        async with AsyncClient() as client:
            response = await client.get(url, headers=self.headers)
        response = response.json()
        return response

    async def daily_viral_songs(self, country_code: str, date_: str = "latest") -> list:
        """
        :param country_code: Country code of the country eg. IN -> India
        :param date_: To get data for a specific date, By default its Latest. eg. 2022-12-24

        :return: a list of daily viral 200 songs from spotify charts.
        """

        url = f"https://charts-spotify-com-service.spotify.com/auth/v0/charts/viral-{country_code.lower()}-daily/{date_}"

        async with AsyncClient() as client:
            response = await client.get(url, headers=self.headers)
        response = response.json()
        return response

    async def weekly_top_songs(self, country_code: str, date_="latest") -> list:
        """
        :param country_code: Country code of the country eg. IN -> India
        :param date_: Week Start Date.

        :return: a list of weekly top 200 songs from spotify charts.
        """
        url = f"https://charts-spotify-com-service.spotify.com/auth/v0/charts/regional-{country_code.lower()}-weekly/{date_}"

        async with AsyncClient() as client:
            response = await client.get(url, headers=self.headers)
        response = response.json()
        return response

    async def weekly_top_artists(self, country_code: str, date_="latest") -> list:
        """
        :param country_code: Country code of the country eg. IN -> India
        :param date_: Week Start Date.

        :return: a list of daily top 200 artists from spotify charts.
        """

        url = f"https://charts-spotify-com-service.spotify.com/auth/v0/charts/artist-{country_code.lower()}-weekly/{date_}"

        async with AsyncClient() as client:
            response = await client.get(url, headers=self.headers)
        response = response.json()
        return response

    async def weekly_top_albums(self, country_code: str, date_="latest") -> list:
        """
        :param country_code: Country code of the country eg. IN -> India
        :param date_: To get data for a specific date, By default its Latest. eg. 2022-12-24

        :return: a list of weekly top 200 albums from spotify charts.
        """

        url = f"https://charts-spotify-com-service.spotify.com/auth/v0/charts/album-{country_code.lower()}-daily/{date_}"

        async with AsyncClient() as client:
            response = await client.get(url, headers=self.headers)
        response = response.json()
        return response
