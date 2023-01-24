import json
import os
from datetime import datetime, timedelta
from pathlib import Path

import pytz
import requests
from google.cloud import storage
from jinja2 import Environment, FileSystemLoader
from newspaper import Article
from podgen import Category, Episode, Media, Podcast
from pydub import AudioSegment
from pydub import exceptions as pydube
from pyquery import PyQuery as pq
from pyquery import pyquery
from lxml import etree

CURRENT_DIRECTORY = os.path.dirname(os.path.realpath(__file__))

VOA_URL = "https://learningenglish.voanews.com"
FEED_DOMAIN = "voa.snnm.net"
FEED_URL = "https://{}/".format(FEED_DOMAIN)
FEED_FILE_NAME = "feed.rss"


def main():
    now = datetime.now()
    today = now.strftime("%m/%d/%Y")
    today_str = now.strftime("%Y%m%d")
    d = pq(VOA_URL)
    articles = get_article_meta(d)
    download_audio_data(articles)
    combined = AudioSegment.empty()
    for i, a in enumerate(articles):
        try:
            audio_data = AudioSegment.from_mp3(
                "{}/audios/{}".format(CURRENT_DIRECTORY, a["file_name"])
            )
        except pydube.CouldntDecodeError:
            print("failed to load audio data: {}".format(a["file_name"]))
            continue
        start_point = get_start_point_min_sec(combined.duration_seconds)
        articles[i]["start_point"] = start_point
        jingle = AudioSegment.from_mp3("{}/jingle.mp3".format(CURRENT_DIRECTORY))
        combined += jingle
        combined += audio_data
    combined.export(
        "{}/episodes/{}.mp3".format(CURRENT_DIRECTORY, today_str), format="mp3"
    )
    write_file_gcs("episodes/{}.mp3".format(today_str))
    file_size = (
        Path("{}/episodes/{}.mp3".format(CURRENT_DIRECTORY, today_str)).stat().st_size
    )
    with open("{}/episodes/{}.json".format(CURRENT_DIRECTORY, today_str), "w") as f:
        f.write(
            json.dumps(
                {
                    "articles": articles,
                    "date": today,
                    "file_size": file_size,
                    "file_name": today_str,
                }
            )
        )
    write_file_gcs("episodes/{}.json".format(today_str))
    generate_html(today_str)
    write_file_gcs("htmls/{}.html".format(today_str))
    p = init_podcast()
    for episode_data in get_episodes():
        p.episodes += [
            Episode(
                title="VOA digest of {}".format(episode_data["date"]),
                media=Media(
                    "{}episodes/{}.mp3".format(FEED_URL, episode_data["file_name"]),
                    int(episode_data["file_size"]),
                ),
                summary="VOA digest of {}".format(episode_data["date"]),
                long_summary=generate_long_summary(episode_data["articles"]),
                publication_date=datetime.strptime(
                    episode_data["date"], "%m/%d/%Y"
                ).astimezone(pytz.utc),
            )
        ]
    p.rss_file("{}/{}".format(CURRENT_DIRECTORY, FEED_FILE_NAME))
    write_file_gcs(FEED_FILE_NAME)


def sub():
    feed_file_name = "feed-article.rss"
    p = Podcast()
    p.name = "(short articles) VOA pod cast with transcript"
    p.description = "(short articles) VOA pod cast with full transcript links"
    p.website = FEED_URL
    p.language = "en"
    p.feed_url = "{}{}".format(FEED_URL, feed_file_name)
    p.category = Category("Education", "Language Courses")
    p.explicit = False
    p.complete = False
    already_used = []
    counter = 0
    for episode_data in get_episodes():
        for article in episode_data["articles"]:
            if not article["file_name"] in already_used:
                try:
                    file_size = (
                        Path(
                            "{}/audios/{}".format(
                                CURRENT_DIRECTORY, article["file_name"]
                            )
                        )
                        .stat()
                        .st_size
                    )
                except FileNotFoundError:
                    continue
                p.episodes += [
                    Episode(
                        title="{}".format(article["title"]),
                        media=Media(article["media_url"].split("?")[0], int(file_size)),
                        summary=article["body"][:200],
                        long_summary="<br /><br />".join(article["body"].split("\n")),
                        publication_date=datetime.strptime(article["date"], "%m/%d/%Y"),
                    )
                ]
                already_used.append(article["file_name"])
                counter += 1
        if counter > 100:
            break
    p.rss_file(feed_file_name)
    write_file_gcs(feed_file_name)
    sub()


def generate_html(file_name):
    env = Environment(loader=FileSystemLoader("{}/templates".format(CURRENT_DIRECTORY)))
    template = env.get_template("episode.html")
    with open("{}/episodes/{}.json".format(CURRENT_DIRECTORY, file_name), "r") as f:
        episode_data = json.loads(f.read())
        title = "VOA digest of {}".format(episode_data["date"])
        articles = episode_data["articles"]
        for i, a in enumerate(articles):
            paragraphs = []
            for p in a["body"].split("\n"):
                if p.startswith("_") and p.endswith("_"):
                    continue
                paragraphs.append(p)
            articles[i]["paragraphs"] = paragraphs
        output_from_parsed_template = template.render(title=title, articles=articles)
    # to save the results
    with open("{}/htmls/{}.html".format(CURRENT_DIRECTORY, file_name), "w") as f:
        f.write(output_from_parsed_template)


def get_start_point_min_sec(seconds):
    sec = timedelta(seconds=seconds)
    d = datetime(1, 1, 1) + sec
    if seconds > 3600:
        return "{:02d}:{:02d}:{:02d}".format(d.hour, d.minute, d.second)
    else:
        return "{:02d}:{:02d}".format(d.minute, d.second)


def download_audio_data(articles: list):
    for a in articles:
        if (
            Path("{}/audios/{}".format(CURRENT_DIRECTORY, a["file_name"])).is_file()
            is False
        ):
            print("getting audio file: {}".format(a["file_name"]))
            audio = requests.get(a["media_url"])
            with open(
                "{}/audios/{}".format(CURRENT_DIRECTORY, a["file_name"]), "wb"
            ) as f:
                f.write(audio.content)
        else:
            print("{}: file exists".format(a["file_name"]))


def get_episodes() -> list:
    episodes = []
    storage_client = storage.Client()
    bucket = storage_client.get_bucket(FEED_DOMAIN)
    blobs = bucket.list_blobs()
    counter = 0
    for jsonf in sorted([b.name for b in blobs if "json" in b.name], reverse=True):
        blob = bucket.blob(jsonf)
        episode_data = json.loads(blob.download_as_string())
        episodes.append(episode_data)
        counter += 1
        if counter > 30:
            break
    return episodes


def write_file_gcs(file_path: str):
    # save file as a public read object
    storage_client = storage.Client()
    bucket = storage_client.get_bucket(FEED_DOMAIN)
    blob = bucket.blob(file_path)
    blob.upload_from_filename("{}/{}".format(CURRENT_DIRECTORY, file_path))
    blob.make_public()


def generate_long_summary(articles: list) -> str:
    long_summary = ""
    for a in articles:
        if "start_point" in a:
            long_summary += "<h2>[{}]</h2>".format(a["start_point"])
        long_summary += "<a href='{}'>{}</a><p>{}</p>--------<br /><br />".format(
            a["url"], a["title"], a["body"][:200]
        )
    return long_summary


def init_podcast() -> Podcast:
    p = Podcast()
    p.name = "VOA pod cast with transcript"
    p.description = "VOA pod cast with full transcript links"
    p.website = FEED_URL
    p.language = "en"
    p.feed_url = "{}{}".format(FEED_URL, FEED_FILE_NAME)
    p.category = Category("Education", "Language Courses")
    p.explicit = False
    p.complete = False
    return p


def get_article_meta(d: pyquery.PyQuery) -> list:
    articles = []
    for e in d(
        "#wrowblock-36256_81 div.media-block a"
    ).items():
        article_data = dict()
        article_data["url"] = VOA_URL + e.attr["href"]
        article_instance = Article(article_data["url"])
        article_instance.download()
        try:
            article_instance.parse()
        except:
            continue
        article_data["title"] = article_instance.title
        article_data["body"] = article_instance.text
        try:
            article_d = pq(article_instance.html)
        except etree.ParserError:
            print("skipping: {}, parser error".format(article_instance.title))
            continue
        article_category = article_d.find("div.category a").text()
        if article_category.lower() == "american stories":
            print("skipping: {}".format(article_category))
            continue
        article_data["media_url"] = article_d.find(
            "#article-content div.inner ul.subitems li.subitem a"
        ).attr("href")
        try:
            article_data["file_name"] = (
                article_data["media_url"].split("/")[-1].split("?")[0]
            )
        except AttributeError:
            continue
        articles.append(article_data)
    return articles


def get_article_body(d: pyquery.PyQuery) -> str:
    body_text = ""
    for e in d("#article-content p"):
        if e.text:
            body_text += e.text
        if body_text.endswith("\n") is False:
            body_text += "\n"
    return body_text


if __name__ == "__main__":
    main()
