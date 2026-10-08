FROM nginx:1.27-alpine

# Templates are rendered into /etc/nginx/conf.d at start-up (ORIGIN_VERIFY_SECRET).
COPY templates/ /etc/nginx/templates/
COPY security-headers.conf /etc/nginx/snippets/security-headers.conf
RUN rm -f /etc/nginx/conf.d/default.conf
# Next.js static export (dashboard/out). The CDK web stack copies it here when bundling;
# for local `docker compose up`, copy it yourself — see README.
COPY static/ /usr/share/nginx/html/
