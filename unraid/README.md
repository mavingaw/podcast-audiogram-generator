# Running Kinder as an Unraid app

Kinder is managed by Unraid's own template system, so it appears on the Docker
and Dashboard tabs like everything else: its own name, its own icon, and a WebUI
button that opens it.

    unraid/my-Kinder.xml       the template
    unraid/apply-template.php  create/recreate the container from it
    unraid/make_icon.py        render the icon PNG
    unraid/icon.png            the rendered icon

## Installing

    scp unraid/my-Kinder.xml root@SERVER:/boot/config/plugins/dockerMan/templates-user/
    scp unraid/apply-template.php root@SERVER:/mnt/storage/appdata/podcast-audiogram-studio/
    ssh root@SERVER /mnt/storage/appdata/podcast-audiogram-studio/apply-template.php Kinder

The image is built locally and has no registry. Unraid is fine with that:
`CreateDocker.php` only pulls when `doesImageExist()` is false, so **Apply** in
the Docker tab works normally. It will not rebuild from new source — that is
what a rebuild is for:

    docker compose -p src -f docker-compose.gpu.yml build
    ./apply-template.php Kinder

## Four things that are not obvious

**`xmlToCommand()` emits `docker create`, not `docker run`.** The Docker tab
starts the container in a separate step. A script that only runs the returned
command leaves it in `created`, which looks exactly like a container that
crashed on boot. `apply-template.php` starts it and then says whether it stayed
up.

**Unraid keeps icons in two places**, and neither is the one that looks right:

    /var/lib/docker/unraid/images/<Name>-icon.png                 persistent
    /usr/local/emhttp/state/plugins/.../images/<Name>-icon.png    served (a symlink to /var/local/emhttp)

`getIcon()` downloads the template's `<Icon>` URL into the first and copies it
to the second. Both must exist, named exactly `<Name>-icon.png`.

**Templates are matched by repository, normalised.** `getTemplateValue()`
compares against `DockerUtil::ensureImageTag()`, so `podcast-audiogram-studio:gpu`
is looked up as `library/podcast-audiogram-studio:gpu`. Querying with the plain
name returns null and looks like a broken template.

**`getAllInfo()` is cached** in `docker.json`. After changing a template, call
`getAllInfo(true)` or the Docker tab keeps showing the old icon and URL.

## Autostart

Unraid's autostart list is a plain file of container names:

    /var/lib/docker/unraid-autostart

`Kinder` is in it, so it comes back when the array starts.

## The icon

`make_icon.py` renders it with nothing but `zlib` and `struct` — this server has
no ImageMagick, no librsvg and no Pillow, and adding one to draw a 256-pixel
square once would be silly. The template points at the copy Kinder serves
itself (`/brand/kinder-icon.png`), so the icon and the app can never disagree
about what Kinder looks like.
