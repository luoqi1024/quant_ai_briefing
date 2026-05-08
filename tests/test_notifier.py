from src.config import Settings
from src.notifier import MARKDOWN_CONTENT_LIMIT_BYTES, WeComNotifier, _split_markdown_by_bytes


class Response:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


class SuccessfulSession:
    def __init__(self):
        self.posts = []

    def get(self, *args, **kwargs):
        return Response({"errcode": 0, "access_token": "token"})

    def post(self, *args, **kwargs):
        self.last_post = (args, kwargs)
        self.posts.append((args, kwargs))
        return Response({"errcode": 0})


class FailingSession:
    def get(self, *args, **kwargs):
        raise RuntimeError("network down")


def test_notifier_returns_false_when_unconfigured():
    notifier = WeComNotifier(settings=Settings(), session=SuccessfulSession())

    assert notifier.send_markdown("hello") is False


def test_notifier_returns_false_when_api_fails():
    notifier = WeComNotifier(
        settings=Settings(
            wecom_corpid="corp",
            wecom_agentid="1000001",
            wecom_secret="secret",
        ),
        session=FailingSession(),
    )

    assert notifier.send_markdown("hello") is False


def test_notifier_sends_markdown_with_mock_session():
    session = SuccessfulSession()
    notifier = WeComNotifier(
        settings=Settings(
            wecom_corpid="corp",
            wecom_agentid="1000001",
            wecom_secret="secret",
        ),
        session=session,
    )

    assert notifier.send_markdown("hello") is True
    assert session.last_post[1]["json"]["msgtype"] == "markdown"
    assert session.last_post[1]["json"]["markdown"]["content"] == "hello"


def test_notifier_splits_long_markdown_messages():
    session = SuccessfulSession()
    notifier = WeComNotifier(
        settings=Settings(
            wecom_corpid="corp",
            wecom_agentid="1000001",
            wecom_secret="secret",
        ),
        session=session,
    )
    content = "### 标题\n\n" + ("这是一段较长的中文日报内容。\n" * 160)

    assert notifier.send_markdown(content) is True
    assert len(session.posts) > 1
    for _args, kwargs in session.posts:
        sent = kwargs["json"]["markdown"]["content"]
        assert len(sent.encode("utf-8")) <= MARKDOWN_CONTENT_LIMIT_BYTES + 120
        assert "投资日报（第 " in sent


def test_split_markdown_by_bytes_is_utf8_safe():
    content = "中文" * 1000

    chunks = _split_markdown_by_bytes(content, max_bytes=180)

    assert "".join(chunks) == content
    assert all(len(chunk.encode("utf-8")) <= 180 for chunk in chunks)
