# Prologin-SADM is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# Prologin-SADM is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with Prologin-SADM.  If not, see <http://www.gnu.org/licenses/>.

import json
import urllib.parse
import urllib.request


class BaseError(Exception):
    """Base class for all exceptions here."""
    pass

class InternalError(BaseError):
    """Raised when there is a protocol failure somewhere."""
    pass

class RemoteError(BaseError):
    """Raised when the remote procedure raised an error."""
    def __init__(self, type, message):
        self.type = type
        self.message = message
        super(RemoteError, self).__init__(type, message)


class Client:
    """RPC client: connect to a server and perform remote calls."""

    # TODO: add authentication support.

    def __init__(self, base_url):
        self.base_url = base_url

    def _extract_result(self, req):
        """Get a response line from a request, parse it and return it.
        """
        line = req.readline()
        return json.loads(line.strip().decode('ascii'))

    def _handle_generator(self, req):
        """Handle a remote generator call, return a generator for its results.

        The first line for the remote call must have already been got and
        parsed. Raise a RemoteError if the remote call raises an error. Raise
        an InternalError for anything else.
        """
        while True:
            result = self._extract_result(req)

            if result['type'] == 'result':
                # There is nothing more to do than returning the actual
                # result.
                yield result['data']

            elif result['type'] == 'stop':
                break

            elif result['type'] == 'exception':
                # Just raise a RemoteError with interesting data.
                self._handle_exception(result)

            else:
                # There should not be any other possibility.
                raise InternalError(
                    'Invalid result type: {}'.format(result['type'])
                )

    def _handle_exception(self, data):
        """Handle an exception from a remote call."""
        raise RemoteError(data['exn_type'], data['exn_message'])

    def _call_method(self, method, args, kwargs):
        """Call the remote `method` passing `args` and `kwargs` to it.

        `args` must be a JSON-serializable list of positional arguments while
        `kwargs` must be a JSON-serializable dictionarry of keyword arguments.

        Depending on what happens in the remote method, return a result, a
        generator or raise a RemoteError. Raise an InternalError for anything
        else.
        """
        # Serialize arguments and send the request...
        arguments = {
            'args': args,
            'kwargs': kwargs,
        }
        try:
            req_data = json.dumps(arguments)
        except (TypeError, ValueError):
            raise ValueError('non serializable argument types')

        full_url = urllib.parse.urljoin(
            self.base_url, 'call/{}'.format(method)
        )
        data = '{}\n'.format(req_data).encode('ascii')
        req = urllib.request.urlopen(full_url, data)

        if req.getcode() == 200:
            # The remote call returned: we can have a result, a generator or an
            # exception.
            with req:
                result = self._extract_result(req)

                if result['type'] == 'result':
                    # There is nothing more to do than returning the actual
                    # result.
                    return result['data']

                elif result['type'] == 'generator':
                    # Returning a generator that do the work is not
                    # straightforward: delegate it.
                    return self._handle_generator(req)

                elif result['type'] == 'exception':
                    # Just raise a RemoteError with interesting data.
                    self._handle_exception(result)

                else:
                    # There should not be any other possibility.
                    raise InternalError(
                        'Invalid result type: {}'.format(result['type'])
                    )
        else:
            # Something went wrong before reaching the remote procedure...
            raise InternalError(req.text)

    def __getattr__(self, method):
        """Return a callable to invoke a remote procedure."""

        def proxy(*args, **kwargs):
            return self._call_method(method, args, kwargs)

        return proxy


class MetaClient:

    def __init__(self, client):
        self.client = client