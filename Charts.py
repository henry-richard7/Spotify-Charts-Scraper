import time
from seleniumwire import webdriver
from selenium.webdriver.common.by import By
from seleniumwire.utils import decode
from selenium.webdriver import FirefoxOptions
import json
import httpx
import configparser

parser = configparser.ConfigParser()
parser.read("config.ini")

email = parser["SPOTIFY"]["email"]
password = parser["SPOTIFY"]["password"]


def check_token():
    url = "https://api.spotify.com/v1/me"
    token = open("token.txt", 'r').read()
    headers = {
        "authorization": f"Bearer {token}",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:107.0) Gecko/20100101 Firefox/107.0",
    }
    http_s = httpx.Client()
    response = http_s.get(url, headers=headers)
    if response.status_code == 200:
        return True
    else:
        return False


def charts_token():
    fireFoxOptions = FirefoxOptions()
    fireFoxOptions.headless = False
    driver = webdriver.Firefox(options=fireFoxOptions)

    driver.get(
        "https://accounts.spotify.com/en/login?continue=https%3A%2F%2Fcharts.spotify.com/login"
    )
    driver.find_element(By.ID, "login-username").send_keys(email)
    driver.find_element(By.ID, "login-password").send_keys(password)
    driver.find_element(By.ID, "login-button").click()
    time.sleep(10)
    for request in driver.requests:
        if request.url.startswith("https://accounts.spotify.com/api/token"):
            data = decode(
                request.response.body,
                request.response.headers.get("Content-Encoding", "identity"),
            ).decode("utf8")
            data = json.loads(data)
            driver.quit()
            open("token.txt", 'w').write(data["access_token"])
            return data["access_token"]


def list_countries() -> list:
    """
    :return: List of available countries in spotify.
    """
    countries = json.load(open("countries.json", "r"))
    return countries


def daily_top_songs(country_code: str, date_="latest") -> list:
    """
    :param country_code: Country code of the country eg. IN -> India
    :param date_: To get data for a specific date, By default its Latest. eg. 2022-12-24

    :return: a list of daily top 200 songs from spotify charts.
    """
    token = open("token.txt", 'r').read() if check_token() else charts_token()
    url = f"https://charts-spotify-com-service.spotify.com/auth/v0/charts/regional-{country_code.lower()}-daily/{date_}"
    headers = {
        "authorization": f"Bearer {token}",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:107.0) Gecko/20100101 Firefox/107.0",
    }
    http_s = httpx.Client()
    response = http_s.get(url, headers=headers)
    response = response.json()
    return response


def weekly_top_songs(country_code: str, date_="latest") -> list:
    """
    :param country_code: Country code of the country eg. IN -> India
    :param date_: Week Start Date.

    :return: a list of weekly top 200 songs from spotify charts.
    """
    token = open("token.txt", 'r').read() if check_token() else charts_token()
    url = f"https://charts-spotify-com-service.spotify.com/auth/v0/charts/regional-{country_code.lower()}-weekly/{date_}"
    headers = {
        "authorization": f"Bearer {token}",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:107.0) Gecko/20100101 Firefox/107.0",
    }
    http_s = httpx.Client()
    response = http_s.get(url, headers=headers)
    response = response.json()
    return response


def weekly_top_artists(country_code: str, date_="latest") -> list:
    """
    :param country_code: Country code of the country eg. IN -> India
    :param date_: Week Start Date.

    :return: a list of daily top 200 artists from spotify charts.
    """
    token = open("token.txt", 'r').read() if check_token() else charts_token()
    url = f"https://charts-spotify-com-service.spotify.com/auth/v0/charts/artist-{country_code.lower()}-weekly/{date_}"
    headers = {
        "authorization": f"Bearer {token}",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:107.0) Gecko/20100101 Firefox/107.0",
    }
    http_s = httpx.Client()
    response = http_s.get(url, headers=headers)
    response = response.json()
    return response


def daily_top_artists(country_code: str, date_="latest") -> list:
    """
    :param country_code: Country code of the country eg. IN -> India
    :param date_: To get data for a specific date, By default its Latest. eg. 2022-12-24

    :return: a list of daily top 200 artists from spotify charts.
    """
    token = open("token.txt", 'r').read() if check_token() else charts_token()
    url = f"https://charts-spotify-com-service.spotify.com/auth/v0/charts/artist-{country_code.lower()}-daily/{date_}"
    headers = {
        "authorization": f"Bearer {token}",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:107.0) Gecko/20100101 Firefox/107.0",
    }
    http_s = httpx.Client()
    response = http_s.get(url, headers=headers)
    response = response.json()
    return response


def weekly_top_albums(country_code: str, date_="latest") -> list:
    """
    :param country_code: Country code of the country eg. IN -> India
    :param date_: To get data for a specific date, By default its Latest. eg. 2022-12-24

    :return: a list of weekly top 200 albums from spotify charts.
    """
    token = open("token.txt", 'r').read() if check_token() else charts_token()
    url = f"https://charts-spotify-com-service.spotify.com/auth/v0/charts/album-{country_code.lower()}-daily/{date_}"
    headers = {
        "authorization": f"Bearer {token}",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:107.0) Gecko/20100101 Firefox/107.0",
    }
    http_s = httpx.Client()
    response = http_s.get(url, headers=headers)
    response = response.json()
    return response


def daily_viral_songs(country_code: str, date_="latest") -> list:
    """
    :param country_code: Country code of the country eg. IN -> India
    :param date_: To get data for a specific date, By default its Latest. eg. 2022-12-24

    :return: a list of daily viral 200 songs from spotify charts.
    """
    token = open("token.txt", 'r').read() if check_token() else charts_token()
    url = f"https://charts-spotify-com-service.spotify.com/auth/v0/charts/viral-{country_code.lower()}-daily/{date_}"
    headers = {
        "authorization": f"Bearer {token}",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:107.0) Gecko/20100101 Firefox/107.0",
    }
    http_s = httpx.Client()
    response = http_s.get(url, headers=headers)
    response = response.json()
    return response
