from TikTokLive import TikTokLiveClient
from TikTokLive.types.events import CommentEvent, GiftEvent
import json
import redis
import os

r = redis.Redis.from_url(os.environ['REDIS_URL'])
client = TikTokLiveClient(unique_id='@handle_utente')

@client.on(CommentEvent)
async def on_comment(ev):
    evt = {"type":"comment","user":ev.user.nickname,"text":ev.comment}
    r.publish('chat_events', json.dumps(evt))

@client.on(GiftEvent)
async def on_gift(ev):
    evt = {"type":"gift","user":ev.user.nickname,"gift":ev.gift.name,
           "count":ev.gift.repeat_count}
    r.publish('chat_events', json.dumps(evt))

client.run()
