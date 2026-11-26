import urllib.request


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, url):
        return None


def urlopen_without_redirects(request, *, timeout):
    return urllib.request.build_opener(_NoRedirect()).open(request, timeout=timeout)
