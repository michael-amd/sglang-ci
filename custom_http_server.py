import html
import http.server
import os
import socketserver
import sys
from http import HTTPStatus

DEFAULT_PORT = 8000
SERVER_TITLE = (
    "SGL Benchmark Plots Server"  # Updated title for centralized plots server
)


class CustomHTTPRequestHandler(http.server.SimpleHTTPRequestHandler):
    def list_directory(self, path):
        """Helper to produce a directory listing (absent index.html).
        Return value is either a file object, or None (indicating an
        error). In either case, the headers are sent, making the
        interface the same as for send_head().
        """
        try:
            list_dir = os.listdir(path)
        except OSError:
            self.send_error(HTTPStatus.NOT_FOUND, "No permission to list directory")
            return None

        list_dir.sort()

        r = []
        # Use the custom server title for both the <title> tag and the main <h1> header
        try:
            display_title = html.escape(SERVER_TITLE, quote=False)
        except AttributeError:  # Compatibility for Python < 3.11.4 with html.escape
            display_title = html.escape(SERVER_TITLE)

        r.append(
            '<!DOCTYPE HTML PUBLIC "-//W3C//DTD HTML 4.01//EN" '
            '"http://www.w3.org/TR/html4/strict.dtd">'
        )
        r.append("<html>\n<head>")
        # Ensure encoding is set, default to utf-8 if not found
        current_encoding = getattr(self, "encoding", "utf-8")
        r.append(
            '<meta http-equiv="Content-Type" '
            'content="text/html; charset=%s">' % current_encoding
        )
        r.append(f"<title>{display_title}</title>\n</head>")
        r.append(f"<body>\n<h1>{display_title}</h1>")
        r.append("<hr>\n<ul>")
        for name in list_dir:
            fullname = os.path.join(path, name)
            displayname = linkname = name
            # Append / for directories or @ for symbolic links
            if os.path.isdir(fullname):
                displayname = name + "/"
                linkname = name + "/"
            if os.path.islink(fullname):
                displayname = name + "@"

            # Ensure names are HTML-escaped for security and correctness
            escaped_linkname = html.escape(linkname, quote=False)
            escaped_displayname = html.escape(displayname)

            r.append(
                '<li><a href="%s">%s</a></li>' % (escaped_linkname, escaped_displayname)
            )
        r.append("</ul>\n<hr>\n</body>\n</html>\n")
        # Use the same encoding for the response
        encoded = "".join(r).encode(current_encoding, "surrogateescape")

        import io

        f = io.BytesIO()
        f.write(encoded)
        f.seek(0)
        self.send_response(HTTPStatus.OK)
        # And here for the content-type header
        self.send_header("Content-type", "text/html; charset=%s" % current_encoding)
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        return f


if __name__ == "__main__":
    port_to_use = DEFAULT_PORT
    if len(sys.argv) > 1:
        try:
            port_to_use = int(sys.argv[1])
        except ValueError:
            print(
                f"Warning: Could not parse port '{sys.argv[1]}'. Using default port {DEFAULT_PORT}.",
                file=sys.stderr,
            )

    # The handler will serve files from the CWD where this script is run from.
    # The plots_server.sh script will cd into /mnt/raid/michael/sgl_benchmark_ci/plots_server/
    # which contains organized plots in subdirectories like GROK1/offline/ and GROK1/online/
    Handler = CustomHTTPRequestHandler

    # Bind to 0.0.0.0 to make it accessible from other machines on the network
    with socketserver.TCPServer(("", port_to_use), Handler) as httpd:
        print(
            f"Serving HTTP on 0.0.0.0 port {port_to_use} from directory '{os.getcwd()}'..."
        )
        print(f'Directory listing title will be: "{SERVER_TITLE}"')
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\\nServer stopped.")
            sys.exit(0)
