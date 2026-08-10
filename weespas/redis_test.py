# app/redis_test.py

import redis

r = redis.Redis(host="localhost", port=6379, decode_responses=True)

print(r.ping())