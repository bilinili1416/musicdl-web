$ErrorActionPreference = 'Stop'
$musicdlSourceRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$musicdlArchivePath = Join-Path $musicdlSourceRoot 'deploy-source.zip'
$musicdlSourceFiles = @(
    'server.py', 'lyrics.py', 'requirements.txt', 'Dockerfile', '.dockerignore',
    '.env.example', '.gitignore', '.gitattributes',
    'web/index.html', 'web/styles.css', 'web/lyrics.css', 'web/app.js', 'web/favicon.svg',
    'deploy/entrypoint.sh', 'deploy/package.ps1', 'tests/test_server.py',
    'README.md', 'DEPLOY.md'
)
Add-Type -AssemblyName System.IO.Compression
Add-Type -AssemblyName System.IO.Compression.FileSystem
$musicdlZipStream = [IO.File]::Open($musicdlArchivePath, [IO.FileMode]::Create)
$musicdlZip = [IO.Compression.ZipArchive]::new($musicdlZipStream, [IO.Compression.ZipArchiveMode]::Create)
try {
    foreach ($musicdlRelativePath in $musicdlSourceFiles) {
        $musicdlFullPath = Join-Path $musicdlSourceRoot $musicdlRelativePath
        [IO.Compression.ZipFileExtensions]::CreateEntryFromFile(
            $musicdlZip, $musicdlFullPath, $musicdlRelativePath,
            [IO.Compression.CompressionLevel]::Optimal
        ) | Out-Null
    }
} finally {
    $musicdlZip.Dispose()
    $musicdlZipStream.Dispose()
}
Write-Output ('Deployment source: ' + $musicdlArchivePath)
