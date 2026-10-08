server {
    listen      %ip%:80;
    server_name %domain_idn% %alias_idn%;
    return 301 https://$host$request_uri;
}
