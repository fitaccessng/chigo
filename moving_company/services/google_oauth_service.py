from authlib.integrations.flask_client import OAuth


google_oauth = OAuth()


def configure_google_oauth(app):
    client_id = app.config.get('GOOGLE_CLIENT_ID')
    client_secret = app.config.get('GOOGLE_CLIENT_SECRET')
    if not client_id or not client_secret:
        return

    google_oauth.init_app(app)
    google_oauth.register(
        name='google',
        client_id=client_id,
        client_secret=client_secret,
        server_metadata_url='https://accounts.google.com/.well-known/openid-configuration',
        client_kwargs={
            'scope': 'openid email profile',
            'code_challenge_method': 'S256',
        },
    )