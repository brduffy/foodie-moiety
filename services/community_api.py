"""Community API client — async HTTP interface to the recipe sharing backend."""

import json
import os
import tempfile
import time

from PySide6.QtCore import QByteArray, QObject, QTimer, QUrl, QUrlQuery, Signal
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest

from utils.config import API_BASE_URL, WEBSITE_URL, API_KEY as _API_KEY
_CACHE_TTL = 300  # 5 minutes


class CommunityApiClient(QObject):
    """Async client for the community recipe/book API."""

    page_loaded = Signal(list, object)       # (items, next_cursor_or_None)
    page_error = Signal(str)                 # error message
    thumbnail_loaded = Signal(str, QByteArray)  # (community_id, raw_bytes)
    download_ready = Signal(str, str)        # (community_id, local_zip_path)
    download_error = Signal(str, str)        # (community_id, error_message)
    download_progress = Signal(str, int, int)  # (community_id, received, total)
    upload_ready = Signal(str)                 # recipe_id / item_id
    upload_error = Signal(str)                 # error message
    upload_size_error = Signal(str, float, float)  # (item_type, file_size, max_size)
    upload_progress = Signal(int, int)         # (bytes_sent, bytes_total)
    pending_loaded = Signal(list, object)      # (items, next_cursor_or_None)
    pending_error = Signal(str)                # error message
    review_done = Signal(str, str)             # (item_id, action)
    review_error = Signal(str, str)            # (item_id, error_message)
    subscription_status_loaded = Signal(dict)  # {tier, status, uploadLimit, uploadCount, ...}
    subscription_status_error = Signal(str)    # error message
    checkout_url_ready = Signal(str)           # Stripe checkout URL
    checkout_url_error = Signal(str)           # error message
    upload_limit_error = Signal(int, int, str) # (count, limit, tier)
    recipe_detail_loaded = Signal(dict)         # normalized recipe item with signed zipUrl
    recipe_detail_error = Signal(str)          # error message
    book_detail_loaded = Signal(dict)          # normalized book item with purchase status
    book_detail_error = Signal(str)            # error message
    purchases_loaded = Signal(object)           # set of purchased book IDs
    purchases_error = Signal(str)              # error message
    account_action_done = Signal(str, str)     # (user_id, action)
    account_action_error = Signal(str, str)    # (user_id, error_message)
    suspended_loaded = Signal(list)            # list of user dicts
    suspended_error = Signal(str)              # error message
    producer_items_loaded = Signal(list)       # list of normalized items by uploader
    producer_items_error = Signal(str)         # error message
    report_data_loaded = Signal(dict)          # upload metadata for CSAM/legal reports
    report_data_error = Signal(str)            # error message
    duplicate_check_done = Signal(bool, str)   # (is_duplicate, message)
    duplicate_check_error = Signal(str)        # error message
    tips_loaded = Signal(list, object)         # (tips, next_cursor_or_None)
    tips_error = Signal(str)                   # error message
    tip_submitted = Signal(str)               # tip_id
    tip_submit_error = Signal(str)            # error message
    # Homepage section signals
    stats_loaded = Signal(dict)               # community stats dict
    stats_error = Signal(str)
    carousel_loaded = Signal(list)            # list of normalized items
    carousel_error = Signal(str)
    books_loaded = Signal(list)               # list of normalized book items
    books_error = Signal(str)
    articles_loaded = Signal(list)            # list of normalized article items
    articles_error = Signal(str)
    moieties_loaded = Signal(list)            # list of normalized moiety items
    moieties_error = Signal(str)
    creators_loaded = Signal(list)            # list of creator dicts
    creators_error = Signal(str)
    feed_loaded = Signal(list, object)        # (items, next_cursor_or_None)
    feed_error = Signal(str)
    tags_loaded = Signal(list)               # list of tag strings
    tags_error = Signal(str)
    cuisines_loaded = Signal(list)           # list of cuisine strings
    search_loaded = Signal(list, object)     # (items, next_cursor_or_None)
    search_error = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._nam = QNetworkAccessManager(self)
        self._cache: dict[str, tuple[float, object]] = {}
        self._active_thumbnail_replies: dict[str, QNetworkReply] = {}
        self._active_download_reply: QNetworkReply | None = None
        self._download_id: str | None = None
        self._download_file = None
        self._upload_reply: QNetworkReply | None = None
        self._upload_zip_path: str | None = None
        self._upload_item_type: str = "recipe"
        self._upload_item_id: str | None = None
        self._auth_token: str | None = None

    # ------------------------------------------------------------------
    # Page fetching
    # ------------------------------------------------------------------

    def fetch_page(self, query="", tags=None, sort="recent", cursor=None,
                   limit=20, producers=None, cuisines=None):
        """Fetch a page of community recipes/books from the API."""
        url = QUrl(f"{API_BASE_URL}/recipes")
        q = QUrlQuery()
        if query:
            q.addQueryItem("query", query)
        if tags:
            q.addQueryItem("tags", ",".join(tags))
        if producers:
            q.addQueryItem("producer", ",".join(producers))
        if cuisines:
            q.addQueryItem("cuisines", ",".join(cuisines))
        if sort and sort != "recent":
            q.addQueryItem("sort", sort)
        if cursor:
            q.addQueryItem("cursor", cursor)
        if limit != 20:
            q.addQueryItem("limit", str(limit))
        url.setQuery(q)

        url_str = url.toString()
        cached = self._get_cached(url_str)
        if cached is not None:
            QTimer.singleShot(0, lambda: self._emit_cached(cached))
            return

        request = QNetworkRequest(url)
        request.setTransferTimeout(10_000)
        reply = self._nam.get(request)
        reply.finished.connect(lambda: self._on_page_reply(reply, url_str))

    def _on_page_reply(self, reply, url_str):
        """Handle the API page response."""
        try:
            if reply.error() != QNetworkReply.NetworkError.NoError:
                self.page_error.emit(self._friendly_error(reply))
                return
            data = json.loads(bytes(reply.readAll()))
            items = [self._normalize_item(i) for i in data.get("items", [])]
            next_cursor = data.get("nextCursor")
            self._set_cached(url_str, (items, next_cursor))
            self.page_loaded.emit(items, next_cursor)
        finally:
            reply.deleteLater()

    def _emit_cached(self, cached):
        """Emit page_loaded from cached data."""
        items, next_cursor = cached
        self.page_loaded.emit(items, next_cursor)

    # ------------------------------------------------------------------
    # Homepage section fetching
    # ------------------------------------------------------------------

    def fetch_stats(self):
        """Fetch community stats (recipe/book/creator/download counts)."""
        url = QUrl(f"{API_BASE_URL}/recipes")
        q = QUrlQuery()
        q.addQueryItem("stats", "true")
        url.setQuery(q)
        url_str = url.toString()
        cached = self._get_cached(url_str)
        if cached is not None:
            QTimer.singleShot(0, lambda: self.stats_loaded.emit(cached))
            return
        request = QNetworkRequest(url)
        request.setTransferTimeout(10_000)
        reply = self._nam.get(request)
        reply.finished.connect(lambda: self._on_stats_reply(reply, url_str))

    def _on_stats_reply(self, reply, url_str):
        try:
            if reply.error() != QNetworkReply.NetworkError.NoError:
                self.stats_error.emit(self._friendly_error(reply))
                return
            data = json.loads(bytes(reply.readAll()))
            self._set_cached(url_str, data)
            self.stats_loaded.emit(data)
        finally:
            reply.deleteLater()

    def fetch_carousel(self):
        """Fetch carousel items for the homepage."""
        url = QUrl(f"{API_BASE_URL}/recipes")
        q = QUrlQuery()
        q.addQueryItem("carousel", "true")
        url.setQuery(q)
        url_str = url.toString()
        cached = self._get_cached(url_str)
        if cached is not None:
            QTimer.singleShot(0, lambda: self.carousel_loaded.emit(cached))
            return
        request = QNetworkRequest(url)
        request.setTransferTimeout(10_000)
        reply = self._nam.get(request)
        reply.finished.connect(lambda: self._on_carousel_reply(reply, url_str))

    def _on_carousel_reply(self, reply, url_str):
        try:
            if reply.error() != QNetworkReply.NetworkError.NoError:
                self.carousel_error.emit(self._friendly_error(reply))
                return
            data = json.loads(bytes(reply.readAll()))
            items = [self._normalize_item(i) for i in data.get("items", [])]
            self._set_cached(url_str, items)
            self.carousel_loaded.emit(items)
        finally:
            reply.deleteLater()

    def fetch_books(self, limit=8):
        """Fetch recent books for the homepage row."""
        self._fetch_typed_row("book", limit, self.books_loaded, self.books_error)

    def fetch_articles(self, limit=8):
        """Fetch recent articles for the homepage row."""
        self._fetch_typed_row("article", limit, self.articles_loaded,
                              self.articles_error)

    def fetch_moieties(self, limit=8):
        """Fetch recent moieties for the homepage row."""
        self._fetch_typed_row("moiety", limit, self.moieties_loaded,
                              self.moieties_error)

    def _fetch_typed_row(self, content_type, limit, success_signal, error_signal):
        """Generic helper for fetching a typed row of items."""
        url = QUrl(f"{API_BASE_URL}/recipes")
        q = QUrlQuery()
        if content_type == "moiety":
            q.addQueryItem("moiety", "true")
        else:
            q.addQueryItem("type", content_type)
        q.addQueryItem("limit", str(limit))
        url.setQuery(q)
        url_str = url.toString()
        cached = self._get_cached(url_str)
        if cached is not None:
            QTimer.singleShot(0, lambda: success_signal.emit(cached))
            return
        request = QNetworkRequest(url)
        request.setTransferTimeout(10_000)
        reply = self._nam.get(request)
        reply.finished.connect(
            lambda: self._on_typed_row_reply(reply, url_str, success_signal,
                                             error_signal)
        )

    def _on_typed_row_reply(self, reply, url_str, success_signal, error_signal):
        try:
            if reply.error() != QNetworkReply.NetworkError.NoError:
                error_signal.emit(self._friendly_error(reply))
                return
            data = json.loads(bytes(reply.readAll()))
            items = [self._normalize_item(i) for i in data.get("items", [])]
            self._set_cached(url_str, items)
            success_signal.emit(items)
        finally:
            reply.deleteLater()

    def fetch_creators(self):
        """Fetch creator directory for the homepage."""
        url = QUrl(f"{API_BASE_URL}/recipes")
        q = QUrlQuery()
        q.addQueryItem("listCreators", "true")
        url.setQuery(q)
        url_str = url.toString()
        cached = self._get_cached(url_str)
        if cached is not None:
            QTimer.singleShot(0, lambda: self.creators_loaded.emit(cached))
            return
        request = QNetworkRequest(url)
        request.setTransferTimeout(10_000)
        reply = self._nam.get(request)
        reply.finished.connect(lambda: self._on_creators_reply(reply, url_str))

    def _on_creators_reply(self, reply, url_str):
        try:
            if reply.error() != QNetworkReply.NetworkError.NoError:
                self.creators_error.emit(self._friendly_error(reply))
                return
            data = json.loads(bytes(reply.readAll()))
            creators = data.get("creators", [])
            self._set_cached(url_str, creators)
            self.creators_loaded.emit(creators)
        finally:
            reply.deleteLater()

    def fetch_feed(self, cursor=None):
        """Fetch personalized feed for the homepage."""
        url = QUrl(f"{API_BASE_URL}/recipes")
        q = QUrlQuery()
        q.addQueryItem("feed", "true")
        if cursor:
            q.addQueryItem("cursor", cursor)
        url.setQuery(q)
        url_str = url.toString()
        if not cursor:
            cached = self._get_cached(url_str)
            if cached is not None:
                QTimer.singleShot(0, lambda: self.feed_loaded.emit(*cached))
                return
        request = QNetworkRequest(url)
        request.setTransferTimeout(10_000)
        if self._auth_token:
            request.setRawHeader(
                b"Authorization", f"Bearer {self._auth_token}".encode()
            )
        reply = self._nam.get(request)
        reply.finished.connect(lambda: self._on_feed_reply(reply, url_str))

    def _on_feed_reply(self, reply, url_str):
        try:
            if reply.error() != QNetworkReply.NetworkError.NoError:
                self.feed_error.emit(self._friendly_error(reply))
                return
            data = json.loads(bytes(reply.readAll()))
            items = [self._normalize_item(i) for i in data.get("items", [])]
            next_cursor = data.get("nextCursor")
            self._set_cached(url_str, (items, next_cursor))
            self.feed_loaded.emit(items, next_cursor)
        finally:
            reply.deleteLater()

    # ------------------------------------------------------------------
    # Community tags
    # ------------------------------------------------------------------

    def fetch_tags(self):
        """Fetch all community tags via GET /recipes?listTags=true."""
        url = QUrl(f"{API_BASE_URL}/recipes")
        q = QUrlQuery()
        q.addQueryItem("listTags", "true")
        url.setQuery(q)
        url_str = url.toString()
        cached = self._get_cached(url_str)
        if cached is not None:
            QTimer.singleShot(0, lambda: self.tags_loaded.emit(cached))
            return
        request = QNetworkRequest(url)
        request.setTransferTimeout(10_000)
        reply = self._nam.get(request)
        reply.finished.connect(lambda: self._on_tags_reply(reply, url_str))

    def _on_tags_reply(self, reply, url_str):
        try:
            if reply.error() != QNetworkReply.NetworkError.NoError:
                self.tags_error.emit(self._friendly_error(reply))
                return
            data = json.loads(bytes(reply.readAll()))
            tags = data.get("tags", [])
            self._set_cached(url_str, tags)
            self.tags_loaded.emit(tags)
        finally:
            reply.deleteLater()

    # ------------------------------------------------------------------
    # Community cuisines
    # ------------------------------------------------------------------

    def fetch_cuisines(self):
        """Fetch all community cuisines via GET /recipes?listCuisines=true."""
        url = QUrl(f"{API_BASE_URL}/recipes")
        q = QUrlQuery()
        q.addQueryItem("listCuisines", "true")
        url.setQuery(q)
        url_str = url.toString()
        cached = self._get_cached(url_str)
        if cached is not None:
            QTimer.singleShot(0, lambda: self.cuisines_loaded.emit(cached))
            return
        request = QNetworkRequest(url)
        request.setTransferTimeout(10_000)
        reply = self._nam.get(request)
        reply.finished.connect(lambda: self._on_cuisines_reply(reply, url_str))

    def _on_cuisines_reply(self, reply, url_str):
        try:
            if reply.error() != QNetworkReply.NetworkError.NoError:
                return
            data = json.loads(bytes(reply.readAll()))
            cuisines = data.get("cuisines", [])
            self._set_cached(url_str, cuisines)
            self.cuisines_loaded.emit(cuisines)
        finally:
            reply.deleteLater()

    # ------------------------------------------------------------------
    # Community search
    # ------------------------------------------------------------------

    def fetch_search(self, query="", tags=None, sort="recent", cursor=None,
                     limit=20, cuisines=None):
        """Search community content. Emits search_loaded (not page_loaded)."""
        url = QUrl(f"{API_BASE_URL}/recipes")
        q = QUrlQuery()
        if query:
            q.addQueryItem("query", query)
        if tags:
            q.addQueryItem("tags", ",".join(tags))
        if cuisines:
            q.addQueryItem("cuisines", ",".join(cuisines))
        if sort and sort != "recent":
            q.addQueryItem("sort", sort)
        if cursor:
            q.addQueryItem("cursor", cursor)
        if limit != 20:
            q.addQueryItem("limit", str(limit))
        url.setQuery(q)
        url_str = url.toString()
        cached = self._get_cached(url_str)
        if cached is not None:
            QTimer.singleShot(
                0, lambda: self.search_loaded.emit(*cached)
            )
            return
        request = QNetworkRequest(url)
        request.setTransferTimeout(10_000)
        reply = self._nam.get(request)
        reply.finished.connect(lambda: self._on_search_reply(reply, url_str))

    def _on_search_reply(self, reply, url_str):
        try:
            if reply.error() != QNetworkReply.NetworkError.NoError:
                self.search_error.emit(self._friendly_error(reply))
                return
            data = json.loads(bytes(reply.readAll()))
            items = [self._normalize_item(i) for i in data.get("items", [])]
            next_cursor = data.get("nextCursor")
            self._set_cached(url_str, (items, next_cursor))
            self.search_loaded.emit(items, next_cursor)
        finally:
            reply.deleteLater()

    # ------------------------------------------------------------------
    # Thumbnail fetching
    # ------------------------------------------------------------------

    def fetch_thumbnail(self, community_id, thumbnail_url):
        """Download a thumbnail image asynchronously."""
        if community_id in self._active_thumbnail_replies:
            return
        request = QNetworkRequest(QUrl(thumbnail_url))
        request.setTransferTimeout(10_000)
        reply = self._nam.get(request)
        self._active_thumbnail_replies[community_id] = reply
        reply.finished.connect(
            lambda: self._on_thumbnail_reply(community_id, reply)
        )

    def _on_thumbnail_reply(self, community_id, reply):
        """Handle thumbnail download completion."""
        self._active_thumbnail_replies.pop(community_id, None)
        try:
            if reply.error() != QNetworkReply.NetworkError.NoError:
                return
            self.thumbnail_loaded.emit(community_id, reply.readAll())
        finally:
            reply.deleteLater()

    def cancel_thumbnails(self):
        """Abort all in-flight thumbnail downloads."""
        for reply in self._active_thumbnail_replies.values():
            reply.abort()
            reply.deleteLater()
        self._active_thumbnail_replies.clear()

    # ------------------------------------------------------------------
    # Zip download
    # ------------------------------------------------------------------

    def download_zip(self, community_id, zip_url):
        """Download a recipe/book zip file to a temp location."""
        if self._active_download_reply is not None:
            self.download_error.emit(
                community_id, "A download is already in progress"
            )
            return
        self._download_id = community_id
        self._download_file = tempfile.NamedTemporaryFile(
            suffix=".zip", delete=False
        )
        request = QNetworkRequest(QUrl(zip_url))
        request.setTransferTimeout(60_000)
        reply = self._nam.get(request)
        self._active_download_reply = reply
        reply.readyRead.connect(self._on_download_data)
        reply.downloadProgress.connect(self._on_download_progress)
        reply.finished.connect(self._on_download_finished)

    def _on_download_data(self):
        """Write incoming data to temp file."""
        if self._active_download_reply and self._download_file:
            self._download_file.write(
                bytes(self._active_download_reply.readAll())
            )

    def _on_download_progress(self, received, total):
        """Forward download progress."""
        if self._download_id:
            self.download_progress.emit(self._download_id, received, total)

    def _on_download_finished(self):
        """Handle download completion or failure."""
        reply = self._active_download_reply
        cid = self._download_id
        self._active_download_reply = None
        self._download_id = None
        if reply is None or cid is None:
            return
        try:
            if self._download_file:
                self._download_file.close()
            if reply.error() != QNetworkReply.NetworkError.NoError:
                # Clean up temp file on download failure
                if self._download_file:
                    try:
                        os.remove(self._download_file.name)
                    except OSError:
                        pass
                self.download_error.emit(cid, self._friendly_error(reply))
                return
            self.download_ready.emit(cid, self._download_file.name)
        finally:
            self._download_file = None
            reply.deleteLater()

    def cancel_download(self):
        """Abort in-progress zip download."""
        if self._active_download_reply:
            # Stash temp file path before abort triggers _on_download_finished
            dl_file = self._download_file
            self._active_download_reply.abort()
            # Clean up temp file (abort triggers finished which closes it,
            # but the error path now handles deletion too)
            if dl_file:
                try:
                    os.remove(dl_file.name)
                except OSError:
                    pass

    # ------------------------------------------------------------------
    # Auth token management
    # ------------------------------------------------------------------

    def set_auth_token(self, id_token: str):
        """Store a Cognito ID token for authenticated uploads."""
        self._auth_token = id_token

    def clear_auth_token(self):
        """Clear the stored auth token (sign out)."""
        self._auth_token = None

    # ------------------------------------------------------------------
    # Admin review
    # ------------------------------------------------------------------

    def fetch_pending(self, cursor=None, limit=20):
        """Fetch pending uploads for admin review."""
        url = QUrl(f"{API_BASE_URL}/admin/pending")
        q = QUrlQuery()
        if cursor:
            q.addQueryItem("cursor", cursor)
        if limit != 20:
            q.addQueryItem("limit", str(limit))
        url.setQuery(q)

        request = QNetworkRequest(url)
        request.setRawHeader(b"x-api-key", _API_KEY.encode())
        if self._auth_token:
            request.setRawHeader(
                b"Authorization", f"Bearer {self._auth_token}".encode()
            )
        request.setTransferTimeout(10_000)
        reply = self._nam.get(request)
        reply.finished.connect(lambda: self._on_pending_reply(reply))

    def _on_pending_reply(self, reply):
        """Handle the admin pending response."""
        try:
            if reply.error() != QNetworkReply.NetworkError.NoError:
                self.pending_error.emit(self._friendly_error(reply))
                return
            data = json.loads(bytes(reply.readAll()))
            items = [self._normalize_item(i) for i in data.get("items", [])]
            next_cursor = data.get("nextCursor")
            self.pending_loaded.emit(items, next_cursor)
        finally:
            reply.deleteLater()

    def review_item(self, item_type, item_id, action, reason="",
                    refund_upload=False):
        """Submit an approve/reject_delete/quarantine review decision."""
        url = QUrl(f"{API_BASE_URL}/admin/review")
        request = QNetworkRequest(url)
        request.setRawHeader(b"x-api-key", _API_KEY.encode())
        if self._auth_token:
            request.setRawHeader(
                b"Authorization", f"Bearer {self._auth_token}".encode()
            )
        request.setRawHeader(b"Content-Type", b"application/json")
        request.setTransferTimeout(15_000)
        body = {"type": item_type, "itemId": item_id, "action": action}
        if reason:
            body["reason"] = reason
        if refund_upload:
            body["refundUpload"] = True
        reply = self._nam.post(request, QByteArray(json.dumps(body).encode()))
        reply.finished.connect(
            lambda: self._on_review_reply(reply, item_id, action)
        )

    def _on_review_reply(self, reply, item_id, action):
        """Handle admin review response."""
        try:
            if reply.error() != QNetworkReply.NetworkError.NoError:
                self.review_error.emit(item_id, self._friendly_error(reply))
                return
            self.review_done.emit(item_id, action)
        finally:
            reply.deleteLater()

    def manage_account(self, user_id, action, reason=""):
        """POST /admin/account/manage — suspend, unsuspend, or cancel_subscription."""
        url = QUrl(f"{API_BASE_URL}/admin/account/manage")
        request = QNetworkRequest(url)
        request.setRawHeader(b"x-api-key", _API_KEY.encode())
        if self._auth_token:
            request.setRawHeader(
                b"Authorization", f"Bearer {self._auth_token}".encode()
            )
        request.setRawHeader(b"Content-Type", b"application/json")
        request.setTransferTimeout(15_000)
        body = {"userId": user_id, "action": action}
        if reason:
            body["reason"] = reason
        reply = self._nam.post(request, QByteArray(json.dumps(body).encode()))
        reply.finished.connect(
            lambda: self._on_account_manage_reply(reply, user_id, action)
        )

    def _on_account_manage_reply(self, reply, user_id, action):
        """Handle account management response."""
        try:
            if reply.error() != QNetworkReply.NetworkError.NoError:
                self.account_action_error.emit(
                    user_id, self._friendly_error(reply)
                )
                return
            self.account_action_done.emit(user_id, action)
        finally:
            reply.deleteLater()

    def fetch_suspended(self):
        """GET /admin/suspended — list all currently suspended users."""
        url = QUrl(f"{API_BASE_URL}/admin/suspended")
        request = QNetworkRequest(url)
        request.setRawHeader(b"x-api-key", _API_KEY.encode())
        if self._auth_token:
            request.setRawHeader(
                b"Authorization", f"Bearer {self._auth_token}".encode()
            )
        request.setTransferTimeout(10_000)
        reply = self._nam.get(request)
        reply.finished.connect(lambda: self._on_suspended_reply(reply))

    def _on_suspended_reply(self, reply):
        """Handle suspended users response."""
        try:
            if reply.error() != QNetworkReply.NetworkError.NoError:
                self.suspended_error.emit(self._friendly_error(reply))
                return
            data = json.loads(bytes(reply.readAll()))
            self.suspended_loaded.emit(data.get("users", []))
        finally:
            reply.deleteLater()

    def fetch_report_data(self, community_id):
        """GET /admin/report-data/{id} — gather upload metadata for legal reports."""
        url = QUrl(f"{API_BASE_URL}/admin/report-data/{community_id}")
        print(f"[report-data] GET {url.toString()}")
        request = QNetworkRequest(url)
        request.setRawHeader(b"x-api-key", _API_KEY.encode())
        if self._auth_token:
            request.setRawHeader(
                b"Authorization", f"Bearer {self._auth_token}".encode()
            )
        request.setTransferTimeout(15_000)
        reply = self._nam.get(request)
        reply.finished.connect(lambda: self._on_report_data_reply(reply))

    def _on_report_data_reply(self, reply):
        """Handle report data response."""
        try:
            print(f"[report-data] reply finished, error={reply.error()}")
            if reply.error() != QNetworkReply.NetworkError.NoError:
                err = self._friendly_error(reply)
                print(f"[report-data] error: {err}")
                self.report_data_error.emit(err)
                return
            raw = bytes(reply.readAll())
            print(f"[report-data] response: {raw[:500]}")
            data = json.loads(raw)
            self.report_data_loaded.emit(data)
        finally:
            reply.deleteLater()

    def fetch_producer_items(self, user_id):
        """GET /recipes?uploadedBy={userId} — all published items by a user."""
        url = QUrl(f"{API_BASE_URL}/recipes")
        q = QUrlQuery()
        q.addQueryItem("uploadedBy", user_id)
        url.setQuery(q)
        request = QNetworkRequest(url)
        request.setRawHeader(b"x-api-key", _API_KEY.encode())
        if self._auth_token:
            request.setRawHeader(
                b"Authorization", f"Bearer {self._auth_token}".encode()
            )
        request.setTransferTimeout(15_000)
        reply = self._nam.get(request)
        reply.finished.connect(lambda: self._on_producer_items_reply(reply))

    def _on_producer_items_reply(self, reply):
        """Handle producer items response."""
        try:
            if reply.error() != QNetworkReply.NetworkError.NoError:
                self.producer_items_error.emit(self._friendly_error(reply))
                return
            data = json.loads(bytes(reply.readAll()))
            items = [self._normalize_item(i) for i in data.get("items", [])]
            self.producer_items_loaded.emit(items)
        finally:
            reply.deleteLater()

    # ------------------------------------------------------------------
    # Subscription & checkout
    # ------------------------------------------------------------------

    def fetch_subscription_status(self):
        """GET /subscriptions/status — returns tier, upload count/limit, etc."""
        url = QUrl(f"{API_BASE_URL}/subscriptions/status")
        request = QNetworkRequest(url)
        request.setRawHeader(b"x-api-key", _API_KEY.encode())
        if self._auth_token:
            request.setRawHeader(
                b"Authorization", f"Bearer {self._auth_token}".encode()
            )
        request.setTransferTimeout(10_000)
        reply = self._nam.get(request)
        reply.finished.connect(
            lambda: self._on_subscription_status_reply(reply)
        )

    def _on_subscription_status_reply(self, reply):
        try:
            if reply.error() != QNetworkReply.NetworkError.NoError:
                self.subscription_status_error.emit(self._friendly_error(reply))
                return
            data = json.loads(bytes(reply.readAll()))
            self.subscription_status_loaded.emit(data)
        finally:
            reply.deleteLater()

    def create_subscription_checkout(self):
        """POST /subscriptions/checkout — returns Stripe checkout URL."""
        url = QUrl(f"{API_BASE_URL}/subscriptions/checkout")
        request = QNetworkRequest(url)
        request.setRawHeader(b"x-api-key", _API_KEY.encode())
        if self._auth_token:
            request.setRawHeader(
                b"Authorization", f"Bearer {self._auth_token}".encode()
            )
        request.setRawHeader(b"Content-Type", b"application/json")
        request.setTransferTimeout(15_000)
        body = json.dumps({"source": "desktop"}).encode()
        reply = self._nam.post(request, QByteArray(body))
        reply.finished.connect(
            lambda: self._on_checkout_url_reply(reply)
        )

    def create_portal_session(self):
        """POST /subscriptions/portal — returns Stripe billing portal URL."""
        url = QUrl(f"{API_BASE_URL}/subscriptions/portal")
        request = QNetworkRequest(url)
        request.setRawHeader(b"x-api-key", _API_KEY.encode())
        if self._auth_token:
            request.setRawHeader(
                b"Authorization", f"Bearer {self._auth_token}".encode()
            )
        request.setRawHeader(b"Content-Type", b"application/json")
        request.setTransferTimeout(15_000)
        reply = self._nam.post(request, QByteArray(b"{}"))
        reply.finished.connect(
            lambda: self._on_checkout_url_reply(reply)
        )

    def create_book_checkout(self, book_id):
        """POST /books/{bookId}/checkout — returns Stripe checkout URL for purchase."""
        url = QUrl(f"{API_BASE_URL}/books/{book_id}/checkout")
        request = QNetworkRequest(url)
        request.setRawHeader(b"x-api-key", _API_KEY.encode())
        if self._auth_token:
            request.setRawHeader(
                b"Authorization", f"Bearer {self._auth_token}".encode()
            )
        request.setRawHeader(b"Content-Type", b"application/json")
        request.setTransferTimeout(15_000)
        body = json.dumps({"source": "desktop"}).encode()
        reply = self._nam.post(request, QByteArray(body))
        reply.finished.connect(
            lambda: self._on_checkout_url_reply(reply)
        )

    def fetch_recipe_detail(self, recipe_id):
        """GET /recipes/{recipeId} — returns recipe detail with signed zipUrl."""
        url = QUrl(f"{API_BASE_URL}/recipes/{recipe_id}")
        request = QNetworkRequest(url)
        request.setRawHeader(b"x-api-key", _API_KEY.encode())
        if self._auth_token:
            request.setRawHeader(
                b"Authorization", f"Bearer {self._auth_token}".encode()
            )
        request.setTransferTimeout(10_000)
        reply = self._nam.get(request)
        reply.finished.connect(
            lambda: self._on_recipe_detail_reply(reply)
        )

    def _on_recipe_detail_reply(self, reply):
        try:
            if reply.error() != QNetworkReply.NetworkError.NoError:
                self.recipe_detail_error.emit(self._friendly_error(reply))
                return
            data = json.loads(bytes(reply.readAll()))
            self.recipe_detail_loaded.emit(self._normalize_item(data))
        finally:
            reply.deleteLater()

    def fetch_article_detail(self, article_id):
        """GET /articles/{articleId} — returns article detail with signed zipUrl."""
        url = QUrl(f"{API_BASE_URL}/articles/{article_id}")
        request = QNetworkRequest(url)
        request.setRawHeader(b"x-api-key", _API_KEY.encode())
        if self._auth_token:
            request.setRawHeader(
                b"Authorization", f"Bearer {self._auth_token}".encode()
            )
        request.setTransferTimeout(10_000)
        reply = self._nam.get(request)
        reply.finished.connect(
            lambda: self._on_recipe_detail_reply(reply)
        )

    def fetch_book_detail(self, book_id):
        """GET /books/{bookId} — returns book detail with purchase status."""
        url = QUrl(f"{API_BASE_URL}/books/{book_id}")
        request = QNetworkRequest(url)
        request.setRawHeader(b"x-api-key", _API_KEY.encode())
        if self._auth_token:
            request.setRawHeader(
                b"Authorization", f"Bearer {self._auth_token}".encode()
            )
        request.setTransferTimeout(10_000)
        reply = self._nam.get(request)
        reply.finished.connect(
            lambda: self._on_book_detail_reply(reply)
        )

    def _on_book_detail_reply(self, reply):
        try:
            if reply.error() != QNetworkReply.NetworkError.NoError:
                self.book_detail_error.emit(self._friendly_error(reply))
                return
            data = json.loads(bytes(reply.readAll()))
            self.book_detail_loaded.emit(self._normalize_item(data))
        finally:
            reply.deleteLater()

    def fetch_purchases(self):
        """GET /purchases — returns set of book IDs the user has purchased."""
        url = QUrl(f"{API_BASE_URL}/purchases")
        request = QNetworkRequest(url)
        request.setRawHeader(b"x-api-key", _API_KEY.encode())
        if self._auth_token:
            request.setRawHeader(
                b"Authorization", f"Bearer {self._auth_token}".encode()
            )
        request.setTransferTimeout(10_000)
        reply = self._nam.get(request)
        reply.finished.connect(
            lambda: self._on_purchases_reply(reply)
        )

    def _on_purchases_reply(self, reply):
        try:
            if reply.error() != QNetworkReply.NetworkError.NoError:
                self.purchases_error.emit(self._friendly_error(reply))
                return
            data = json.loads(bytes(reply.readAll()))
            book_ids = {p.get("bookId", "") for p in data.get("purchases", [])}
            book_ids.discard("")
            self.purchases_loaded.emit(book_ids)
        finally:
            reply.deleteLater()

    def _on_checkout_url_reply(self, reply):
        """Handle checkout/portal URL response."""
        try:
            if reply.error() != QNetworkReply.NetworkError.NoError:
                self.checkout_url_error.emit(self._friendly_error(reply))
                return
            data = json.loads(bytes(reply.readAll()))
            url = (
                data.get("checkoutUrl")
                or data.get("portalUrl")
                or ""
            )
            if url:
                self.checkout_url_ready.emit(url)
            else:
                self.checkout_url_error.emit("Server did not return a URL")
        finally:
            reply.deleteLater()

    # ------------------------------------------------------------------
    # Upload (presigned URL flow)
    # ------------------------------------------------------------------

    def check_duplicate_title(self, title):
        """GET /uploads/check-duplicate?title=<title> — pre-upload uniqueness check."""
        url = QUrl(f"{API_BASE_URL}/uploads/check-duplicate")
        q = QUrlQuery()
        q.addQueryItem("title", title)
        url.setQuery(q)
        request = QNetworkRequest(url)
        request.setRawHeader(b"x-api-key", _API_KEY.encode())
        if self._auth_token:
            request.setRawHeader(
                b"Authorization", f"Bearer {self._auth_token}".encode()
            )
        request.setTransferTimeout(10_000)
        reply = self._nam.get(request)
        reply.finished.connect(lambda: self._on_duplicate_check_reply(reply))

    def _on_duplicate_check_reply(self, reply):
        """Handle duplicate check response."""
        try:
            if reply.error() != QNetworkReply.NetworkError.NoError:
                self.duplicate_check_error.emit(self._friendly_error(reply))
                return
            data = json.loads(bytes(reply.readAll()))
            self.duplicate_check_done.emit(
                data.get("duplicate", False),
                data.get("message", ""),
            )
        finally:
            reply.deleteLater()

    def upload_zip(self, zip_path, item_type="recipe", bom_candidate=False):
        """Upload a recipe/book zip via presigned URL.

        Step 1: POST to /recipes/upload to get a presigned S3 URL.
        Step 2: PUT the zip bytes directly to S3.
        """
        if self._upload_reply is not None:
            self.upload_error.emit("An upload is already in progress")
            return
        self._upload_zip_path = zip_path
        self._upload_item_type = item_type

        # Get file size for server-side pre-validation
        try:
            content_length = os.path.getsize(zip_path)
        except OSError:
            content_length = 0

        # Step 1: request presigned URL
        url = QUrl(f"{API_BASE_URL}/recipes/upload")
        request = QNetworkRequest(url)
        request.setRawHeader(b"x-api-key", _API_KEY.encode())
        if self._auth_token:
            request.setRawHeader(
                b"Authorization", f"Bearer {self._auth_token}".encode()
            )
        request.setRawHeader(b"Content-Type", b"application/json")
        request.setTransferTimeout(15_000)
        body = json.dumps({
            "filename": zip_path.rsplit("/", 1)[-1],
            "type": item_type,
            "contentLength": content_length,
            "bookOfMoietyCandidate": bom_candidate,
        }).encode()
        reply = self._nam.post(request, QByteArray(body))
        self._upload_reply = reply
        reply.finished.connect(lambda: self._on_presigned_reply(reply))

    def _cleanup_upload_zip(self):
        """Delete the temp upload zip file if it exists."""
        path = self._upload_zip_path
        self._upload_zip_path = None
        if path:
            try:
                os.remove(path)
            except OSError:
                pass

    def _on_presigned_reply(self, reply):
        """Handle presigned URL response, then PUT the zip."""
        try:
            status = reply.attribute(
                QNetworkRequest.Attribute.HttpStatusCodeAttribute
            )
            if reply.error() != QNetworkReply.NetworkError.NoError:
                self._upload_reply = None
                self._cleanup_upload_zip()
                # 413: file too large — emit structured signal with sizes
                if status == 413:
                    try:
                        body = json.loads(bytes(reply.readAll()))
                        max_size = body.get("maxSize", 0)
                    except Exception:
                        max_size = 0
                    self.upload_size_error.emit(
                        self._upload_item_type, 0.0, float(max_size)
                    )
                elif status == 403:
                    try:
                        body = json.loads(bytes(reply.readAll()))
                    except Exception:
                        body = {}
                    if body.get("suspended"):
                        self.upload_error.emit(
                            "Your account has been suspended"
                        )
                    else:
                        count = body.get("uploadCount", 0)
                        limit = body.get("uploadLimit", 0)
                        tier = body.get("tier", "free")
                        self.upload_limit_error.emit(count, limit, tier)
                else:
                    self.upload_error.emit(self._friendly_error(reply))
                return
            data = json.loads(bytes(reply.readAll()))
            upload_url = data.get("uploadUrl", "")
            self._upload_item_id = data.get("recipeId", "")
            if not upload_url:
                self._upload_reply = None
                self._cleanup_upload_zip()
                self.upload_error.emit("Server did not return an upload URL")
                return
        finally:
            reply.deleteLater()

        # Step 2: PUT zip to S3
        try:
            with open(self._upload_zip_path, "rb") as f:
                zip_bytes = f.read()
        except OSError as e:
            self._upload_reply = None
            self._cleanup_upload_zip()
            self.upload_error.emit(f"Could not read zip file: {e}")
            return

        request = QNetworkRequest(QUrl(upload_url))
        request.setRawHeader(b"Content-Type", b"application/zip")
        request.setTransferTimeout(300_000)  # 5 min for large files
        put_reply = self._nam.put(request, QByteArray(zip_bytes))
        self._upload_reply = put_reply
        put_reply.uploadProgress.connect(self._on_upload_progress)
        put_reply.finished.connect(lambda: self._on_upload_finished(put_reply))

    def _on_upload_progress(self, sent, total):
        """Forward upload progress."""
        self.upload_progress.emit(sent, total)

    def _on_upload_finished(self, reply):
        """Handle S3 PUT completion."""
        item_id = self._upload_item_id or ""
        self._upload_reply = None
        self._upload_item_id = None
        self._cleanup_upload_zip()
        try:
            if reply.error() != QNetworkReply.NetworkError.NoError:
                self.upload_error.emit(self._friendly_error(reply))
                return
            self.invalidate_cache()
            self.upload_ready.emit(item_id)
        finally:
            reply.deleteLater()

    def cancel_upload(self):
        """Abort in-progress upload."""
        self._cleanup_upload_zip()
        if self._upload_reply:
            self._upload_reply.abort()

    # ------------------------------------------------------------------
    # Cache
    # ------------------------------------------------------------------

    def _get_cached(self, url):
        entry = self._cache.get(url)
        if entry and (time.time() - entry[0]) < _CACHE_TTL:
            return entry[1]
        if entry:
            del self._cache[url]
        return None

    def _set_cached(self, url, data):
        self._cache[url] = (time.time(), data)

    def invalidate_cache(self):
        """Clear all cached API responses."""
        self._cache.clear()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _normalize_item(item):
        """Convert camelCase API fields to the dict format RecipeCard expects."""
        return {
            "community_id": item.get("recipeId") or item.get("articleId") or item.get("bookId", ""),
            "id": 0,
            "title": item.get("title", "Untitled"),
            "description": item.get("descriptionPlain", ""),
            "main_image_path": "",
            "thumbnail_url": item.get("thumbnailUrl", ""),
            "zip_url": item.get("zipUrl", ""),
            "type": item.get("type", "recipe"),
            "tags": item.get("tags", []),
            "difficulty": item.get("difficulty", ""),
            "total_time_min": item.get("totalTimeMin"),
            "prep_time_min": item.get("prepTimeMin"),
            "cook_time_min": item.get("cookTimeMin"),
            "cuisine_type": item.get("cuisineType", ""),
            "producer": item.get("producer", ""),
            "uploaded_at": item.get("uploadedAt", ""),
            "ingredient_count": item.get("ingredientCount"),
            "recipe_count": item.get("recipeCount"),
            "category_count": item.get("categoryCount"),
            "categories": item.get("categories", []),
            "price_type": item.get("priceType", "free"),
            "price_cents": item.get("priceCents", 0),
            "is_purchased": item.get("isPurchased", False),
            "is_creator": item.get("isCreator", False),
            "uploaded_by": item.get("uploadedBy") or "",
            "community_origin_id": item.get("communityOriginId") or "",
            "community_origin_uploader": item.get("communityOriginUploader") or "",
            "community_origin_producer": item.get("communityOriginProducer") or "",
            "is_moiety": item.get("isMoiety", False),
            "bookOfMoietyCandidate": item.get("bookOfMoietyCandidate", False),
        }

    # ------------------------------------------------------------------
    # Recipe tips
    # ------------------------------------------------------------------

    def fetch_tips(self, recipe_id, limit=20, cursor=None):
        """GET /recipes/{recipeId}/tips — fetch approved tips for a recipe."""
        url = QUrl(f"{API_BASE_URL}/recipes/{recipe_id}/tips")
        q = QUrlQuery()
        q.addQueryItem("limit", str(limit))
        if cursor:
            q.addQueryItem("cursor", cursor)
        url.setQuery(q)
        request = QNetworkRequest(url)
        request.setRawHeader(b"x-api-key", _API_KEY.encode())
        request.setTransferTimeout(10_000)
        reply = self._nam.get(request)
        reply.finished.connect(lambda: self._on_tips_reply(reply))

    def _on_tips_reply(self, reply):
        try:
            if reply.error() != QNetworkReply.NetworkError.NoError:
                self.tips_error.emit(self._friendly_error(reply))
                return
            data = json.loads(bytes(reply.readAll()))
            self.tips_loaded.emit(
                data.get("tips", []),
                data.get("nextCursor"),
            )
        finally:
            reply.deleteLater()

    def submit_tip(self, recipe_id, text):
        """POST /recipes/{recipeId}/tips — submit a tip for moderation."""
        url = QUrl(f"{API_BASE_URL}/recipes/{recipe_id}/tips")
        request = QNetworkRequest(url)
        request.setRawHeader(b"x-api-key", _API_KEY.encode())
        if self._auth_token:
            request.setRawHeader(
                b"Authorization", f"Bearer {self._auth_token}".encode()
            )
        request.setRawHeader(b"Content-Type", b"application/json")
        request.setTransferTimeout(10_000)
        body = json.dumps({"text": text}).encode()
        reply = self._nam.post(request, body)
        reply.finished.connect(lambda: self._on_tip_submit_reply(reply))

    def _on_tip_submit_reply(self, reply):
        try:
            if reply.error() != QNetworkReply.NetworkError.NoError:
                self.tip_submit_error.emit(self._friendly_error(reply))
                return
            data = json.loads(bytes(reply.readAll()))
            self.tip_submitted.emit(data.get("tipId", ""))
        finally:
            reply.deleteLater()

    @staticmethod
    def _friendly_error(reply):
        """Return a user-friendly error message from a QNetworkReply."""
        err = reply.error()
        if err == QNetworkReply.NetworkError.HostNotFoundError:
            return "Could not reach the server \u2014 check your internet connection"
        if err == QNetworkReply.NetworkError.OperationCanceledError:
            return "Request timed out — please check your internet connection"
        if err == QNetworkReply.NetworkError.TimeoutError:
            return "Request timed out — please check your internet connection"
        if err == QNetworkReply.NetworkError.ConnectionRefusedError:
            return "Could not reach the server — please try again later"
        status = reply.attribute(QNetworkRequest.Attribute.HttpStatusCodeAttribute)
        if status and status >= 400:
            # Try to extract error message from JSON response body
            try:
                body = json.loads(bytes(reply.readAll()))
                msg = body.get("error") or body.get("message", "")
                if msg:
                    return msg
            except Exception:
                pass
            return f"Server error (HTTP {status})"
        return reply.errorString()
