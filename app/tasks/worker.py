import os

from redis import Redis
from rq import Worker, Queue


os.environ["START_INLINE_WORKER"] = "0"


def main():
    from app import _load_dotenv
    from pathlib import Path
    _load_dotenv(str(Path(__file__).resolve().parents[2] / ".env"))
    redis_url = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
    conn = Redis.from_url(redis_url)
    listen = ["default"]
    queues = [Queue(name, connection=conn) for name in listen]
    worker = Worker(queues, connection=conn)
    worker.work()


if __name__ == "__main__":
    main()
