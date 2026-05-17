FROM nginx:stable-alpine

COPY ./frontend /usr/share/nginx/html
COPY ./compose/production/nginx/nginx.conf /etc/nginx/conf.d/default.conf

EXPOSE 80
CMD ["nginx", "-g", "daemon off;"]
