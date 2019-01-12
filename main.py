from datetime import datetime, timedelta
import json
from pathlib import Path

from pyquery import PyQuery as pq
from pyquery import pyquery
import requests
from pydub import AudioSegment
from podgen import Podcast, Episode, Media, Category
from jinja2 import Environment, FileSystemLoader

from google.cloud import storage

VOA_URL = "https://learningenglish.voanews.com"
FEED_DOMAIN = "voa.snnm.net"
FEED_URL = "https://{}/".format(FEED_DOMAIN)
FEED_FILE_NAME = "feed.rss"


def main():
    now = datetime.now()
    today = now.strftime('%m/%d/%Y')
    today_str = now.strftime('%Y%m%d')
    # pyquery generates DOM
    d = pq(VOA_URL)
    # parse articles data from the DOM
    articles = get_article_meta(d)
    download_audio_data(articles)
    combined = AudioSegment.empty()
    for i, a in enumerate(articles):
        start_point = get_start_point_min_sec(combined.duration_seconds)
        articles[i]['start_point'] = start_point
        jingle = AudioSegment.from_mp3('jingle.mp3')
        combined += jingle
        audio_data = AudioSegment.from_mp3('./audios/{}'.format(a['file_name']))
        combined += audio_data
    combined.export('episodes/{}.mp3'.format(today_str), format='mp3')
    write_file_gcs('episodes/{}.mp3'.format(today_str))
    file_size = Path('episodes/{}.mp3'.format(today_str)).stat().st_size
    with open('episodes/{}.json'.format(today_str), 'w') as f:
        f.write(json.dumps({'articles': articles, 'date': today, 'file_size': file_size, 'file_name': today_str}))
    write_file_gcs('episodes/{}.json'.format(today_str))
    generate_html(today_str)
    write_file_gcs('htmls/{}.html'.format(today_str))
    p = init_podcast()
    for episode_data in get_episodes():
        p.episodes += [
            Episode(title="VOA digest of {}".format(episode_data['date']),
                    media=Media("{}episodes/{}.mp3".format(FEED_URL, episode_data['file_name']),
                                int(episode_data['file_size'])),
                    summary="VOA digest of {}".format(episode_data['date']),
                    long_summary=generate_long_summary(episode_data['articles']),
                    )
        ]
    p.rss_file(FEED_FILE_NAME)
    write_file_gcs(FEED_FILE_NAME)


def generate_html(file_name):
    env = Environment(loader=FileSystemLoader('templates'))
    template = env.get_template('episode.html')
    with open('episodes/{}.json'.format(file_name), 'r') as f:
        episode_data = json.loads(f.read())
        title = "VOA digest of {}".format(episode_data['date'])
        articles = episode_data['articles']
        for i, a in enumerate(articles):
            articles[i]['paragraphs'] = a['body'].split('\n')
        output_from_parsed_template = template.render(title=title, articles=articles)
    # to save the results
    with open("htmls/{}.html".format(file_name), "w") as f:
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
        if Path('audios/{}'.format(a['file_name'])).is_file() is False:
            print("getting audio file: {}".format(a['file_name']))
            audio = requests.get(a['media_url'])
            with open('audios/{}'.format(a['file_name']), 'wb') as f:
                f.write(audio.content)
        else:
            print("{}: file exists".format(a['file_name']))


def get_episodes() -> list:
    episodes = []
    storage_client = storage.Client()
    bucket = storage_client.get_bucket(FEED_DOMAIN)
    blobs = bucket.list_blobs()
    for jsonf in sorted([b.name for b in blobs if 'json' in b.name], reverse=True):
        blob = bucket.blob(jsonf)
        episode_data = json.loads(blob.download_as_string())
        episodes.append(episode_data)
    return episodes


def write_file_gcs(file_path: str):
    # save file as a public read object
    storage_client = storage.Client()
    bucket = storage_client.get_bucket(FEED_DOMAIN)
    blob = bucket.blob(file_path)
    blob.upload_from_filename(file_path)
    blob.make_public()


def generate_long_summary(articles: list) -> str:
    long_summary = ""
    for a in articles:
        if 'start_point' in a:
            long_summary += "<h2>[{}]</h2>".format(a['start_point'])
        long_summary += "<a href='{}'>{}</a><p>{}</p>--------<br /><br />".format(a['url'], a['title'], a['body'][:200])
    return long_summary


def init_podcast() -> Podcast:
    p = Podcast()
    p.name = "VOA pod cast with transcript"
    p.description = "VOA pod cast with full transcript links"
    p.website = FEED_URL
    p.language = "en"
    p.feed_url = "{}{}".format(FEED_URL, FEED_FILE_NAME)
    p.category = Category('Education', 'Language Courses')
    p.explicit = False
    p.complete = False
    return p


def get_article_meta(d: pyquery.PyQuery) -> list:
    articles = []
    for e in d('div[data-area-id=R1_1] li a span.title').items():
        article_data = dict()
        article_data['url'] = VOA_URL + e.parents('a').attr['href']
        article_data['title'] = e[0].text
        article_d = pq(article_data['url'])
        article_data['body'] = get_article_body(article_d)
        article_data['media_url'] = article_d.find('#article-content div.inner ul.subitems li.subitem a').attr('href')
        article_data['file_name'] = article_data['media_url'].split('/')[-1].split('?')[0]
        articles.append(article_data)
    return articles


def get_article_body(d: pyquery.PyQuery) -> str:
    body_text = ''
    for e in d('#article-content p'):
        if e.text:
            body_text += e.text
        elif body_text.endswith('\n') is False:
            body_text += '\n'
    return body_text


if __name__ == '__main__':
    main()
