import os
import socket

from moving_company import create_app

app = create_app()


def find_available_port(start_port):
    for candidate in range(start_port, 65536):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            try:
                probe.bind(('0.0.0.0', candidate))
            except OSError:
                continue
            return candidate
    raise RuntimeError(f'No available TCP port found starting at {start_port}.')


if __name__ == '__main__':
    requested_port = int(os.environ.get('PORT', '5000'))
    if os.environ.get('WERKZEUG_RUN_MAIN') == 'true':
        port = int(os.environ.get('CHIGO_PORT') or find_available_port(requested_port))
    else:
        port = find_available_port(requested_port)
    os.environ['CHIGO_PORT'] = str(port)
    if port != requested_port:
        print(f'Port {requested_port} is occupied; starting Chigo on port {port}.')
    app.run(debug=True, host='0.0.0.0', port=port)
