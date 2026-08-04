# SSL Certificates Directory for Nginx

Place your Cloudflare Origin CA certificate and private key in this directory:

- `cert.pem`: The Cloudflare Origin CA certificate (PEM format)
- `key.pem`: The private key associated with the certificate (PEM format)

These files are mounted into the Nginx container at `/etc/nginx/certificates/`.
