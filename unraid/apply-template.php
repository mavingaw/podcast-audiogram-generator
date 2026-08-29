#!/usr/bin/php -q
<?php
/*
 * Create (or recreate) a container from its Unraid user template, using
 * Unraid's own xmlToCommand() so the result is byte-for-byte what the Docker
 * tab would build.
 *
 * Why not Unraid's stock scripts/rebuild_container: it never defines $var, so
 * TZ and HOST_HOSTNAME come out empty. This loads state/var.ini first.
 *
 * Why the explicit start: xmlToCommand() emits `docker create`, not
 * `docker run`. The Docker tab starts the container in a separate step
 * afterwards, so a script that only runs the returned command leaves it sitting
 * in `created` — which looks like a container that crashed on boot, and cost an
 * hour of chasing a GPU error that was not the problem.
 *
 *   ./apply-template.php Kinder
 */
$docroot = '/usr/local/emhttp';
require_once "$docroot/plugins/dynamix.docker.manager/include/DockerClient.php";
chdir($docroot);

$var    = parse_ini_file('state/var.ini');          // TZ + HOST_HOSTNAME
$custom = DockerUtil::custom();
$subnet = DockerUtil::network($custom);
$cpus   = DockerUtil::cpus();

$DockerClient    = new DockerClient();
$DockerUpdate    = new DockerUpdate();
$DockerTemplates = new DockerTemplates();

$name = $argv[1] ?? '';
$tmpl = $name ? $DockerTemplates->getUserTemplate($name) : '';
if (!$tmpl) { fwrite(STDERR, "no user template found for '$name'\n"); exit(1); }

[$cmd, $Name, $Repository] = xmlToCommand($tmpl);

if (!$DockerClient->doesImageExist($Repository)) {
    fwrite(STDERR, "image '$Repository' is not present locally - build it first\n");
    exit(1);
}

echo ">> template: $tmpl\n>> recreating '$Name' from template\n";
if ($DockerClient->doesContainerExist($Name)) {
    exec("docker rm -f " . escapeshellarg($Name) . " 2>/dev/null");
}

$rc = 0; $out = [];
exec($cmd . " 2>&1", $out, $rc);
echo implode("\n", $out) . "\n";
if ($rc !== 0) { $DockerClient->flushCaches(); exit($rc); }

// Start it, and say plainly whether it stayed up. A container that exits
// immediately is the interesting case, and it is invisible otherwise.
exec("docker start " . escapeshellarg($Name) . " 2>&1", $startOut, $rc);
if ($rc !== 0) {
    fwrite(STDERR, "failed to start '$Name':\n" . implode("\n", $startOut) . "\n");
    $DockerClient->flushCaches();
    exit($rc);
}

sleep(3);
$state = trim(shell_exec(
    "docker inspect " . escapeshellarg($Name) . " --format '{{.State.Status}}' 2>/dev/null"
) ?? '');
echo ">> $Name is $state\n";
if ($state !== 'running') {
    fwrite(STDERR, "'$Name' did not stay running; last output:\n");
    fwrite(STDERR, shell_exec("docker logs --tail 20 " . escapeshellarg($Name) . " 2>&1"));
    $DockerClient->flushCaches();
    exit(1);
}

$DockerClient->flushCaches();
exit(0);
