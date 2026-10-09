import html
import smtplib
import ssl
from email.message import EmailMessage

from flask import current_app


def send_branded_email(
    recipient,
    subject,
    preheader,
    heading,
    paragraphs,
    action_label,
    action_url,
    metadata=None,
):
    message = EmailMessage()
    message['Subject'] = subject
    message['From'] = current_app.config['MAIL_DEFAULT_SENDER']
    message['To'] = recipient

    plain_paragraphs = '\n\n'.join(paragraphs)
    message.set_content(
        f'{heading}\n\n{plain_paragraphs}\n\n{action_label}: {action_url}\n\n'
        'Chigo Relocations\nhello@chigomove.online'
    )

    safe_paragraphs = ''.join(
        f'<p style="margin:0 0 16px;color:#394b48;font-size:16px;line-height:1.65">'
        f'{html.escape(paragraph)}</p>'
        for paragraph in paragraphs
    )
    message.add_alternative(
        f'''<!doctype html>
<html lang="en">
  <body style="margin:0;background:#f2f6f3;font-family:Arial,Helvetica,sans-serif;color:#183b35">
    <div style="display:none;max-height:0;overflow:hidden;opacity:0">{html.escape(preheader)}</div>
    <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="background:#f2f6f3;padding:32px 12px">
      <tr><td align="center">
        <table role="presentation" width="600" cellspacing="0" cellpadding="0" style="width:100%;max-width:600px;background:#ffffff;border:1px solid #dce7e2">
          <tr><td style="padding:28px 36px;background:#174b40;color:#ffffff">
            <div style="font-size:20px;font-weight:700">CHIGO</div>
            <div style="margin-top:4px;font-size:11px;letter-spacing:2px">RELOCATIONS</div>
          </td></tr>
          <tr><td style="padding:38px 36px 32px">
            <h1 style="margin:0 0 20px;font-size:27px;line-height:1.25;color:#183b35">{html.escape(heading)}</h1>
            {safe_paragraphs}
            <p style="margin:28px 0">
              <a href="{html.escape(action_url, quote=True)}" style="display:inline-block;padding:14px 22px;background:#d8ef70;color:#183b35;text-decoration:none;font-weight:700">{html.escape(action_label)}</a>
            </p>
            <p style="margin:0;color:#64746f;font-size:13px;line-height:1.6">If the button does not work, copy this link into your browser:<br>
              <a href="{html.escape(action_url, quote=True)}" style="color:#176455;word-break:break-all">{html.escape(action_url)}</a>
            </p>
          </td></tr>
          <tr><td style="padding:20px 36px;background:#f7faf8;color:#64746f;font-size:13px;line-height:1.6">
            Chigo Relocations · Moving with care<br>
            <a href="mailto:hello@chigomove.online" style="color:#176455">hello@chigomove.online</a>
          </td></tr>
        </table>
      </td></tr>
    </table>
  </body>
</html>''',
        subtype='html',
    )

    if current_app.testing:
      current_app.extensions.setdefault('outbox', []).append({
        'to': recipient,
        'subject': subject,
            'text': message.get_body(preferencelist=('plain',)).get_content(),
        'html': message.get_body(preferencelist=('html',)).get_content(),
        **(metadata or {}),
      })
      return

    mail_server = current_app.config.get('MAIL_SERVER')
    if not mail_server:
        raise RuntimeError('Email is not configured. Set MAIL_SERVER and mail credentials.')

    mail_username = current_app.config.get('MAIL_USERNAME')
    mail_password = current_app.config.get('MAIL_PASSWORD')
    if current_app.config.get('MAIL_USE_SSL'):
        with smtplib.SMTP_SSL(
            mail_server,
            current_app.config['MAIL_PORT'],
            timeout=10,
            context=ssl.create_default_context(),
        ) as smtp:
            if mail_username and mail_password:
                smtp.login(mail_username, mail_password)
            smtp.send_message(message)
        return

    with smtplib.SMTP(mail_server, current_app.config['MAIL_PORT'], timeout=10) as smtp:
        if current_app.config.get('MAIL_USE_TLS'):
            smtp.starttls(context=ssl.create_default_context())
        if mail_username and mail_password:
            smtp.login(mail_username, mail_password)
        smtp.send_message(message)