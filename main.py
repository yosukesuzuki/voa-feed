import datetime
import json
from pathlib import Path

from pyquery import PyQuery as pq
from pyquery import pyquery
import requests
from pydub import AudioSegment
from podgen import Podcast, Episode, Media, Category

from google.cloud import storage

VOA_URL = "https://learningenglish.voanews.com/"
FEED_DOMAIN = "voa.snnm.net"
FEED_URL = "http://{}/".format(FEED_DOMAIN)
FEED_FILE_NAME = "feed.rss"


def main():
    now = datetime.datetime.now()
    today = now.strftime('%m/%d/%Y')
    today_str = now.strftime('%Y%m%d')
    d = pq(VOA_URL)
    articles = get_article_meta(d)
    for a in articles:
        if Path('audios/{}'.format(a['file_name'])).is_file() is False:
            print("getting audio file: {}".format(a['file_name']))
            audio = requests.get(a['media_url'])
            with open('audios/{}'.format(a['file_name']), 'wb') as f:
                f.write(audio.content)
        else:
            print("{}: file exists".format(a['file_name']))
    combined = AudioSegment.empty()
    for a in articles:
        audio_data = AudioSegment.from_mp3('./audios/{}'.format(a['file_name']))
        combined += audio_data
    combined.export('episodes/{}.mp3'.format(today_str), format='mp3')
    write_file_gcs('episodes/{}.mp3'.format(today_str))
    file_size = Path('episodes/{}.mp3'.format(today_str)).stat().st_size
    with open('episodes/{}.json'.format(today_str), 'w') as f:
        f.write(json.dumps({'articles': articles, 'date': today, 'file_size': file_size, 'file_name': today_str}))
    p = init_podcast()
    storage_client = storage.Client()
    bucket = storage_client.get_bucket(FEED_DOMAIN)
    blobs = bucket.list_blobs()
    for jsonf in sorted([b.name for b in blobs if 'json' in b.name],reverse=True):
        blob = bucket.blob(jsonf)
        episode_data = json.loads(blob.download_as_string())
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
