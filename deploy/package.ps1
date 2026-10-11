$ErrorActionPreference = 'Stop'
$musicdlSourceRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$musicdlArchivePath = Join-Path $musicdlSourceRoot 'deploy-source.zip'
$musicdlSourceFiles = @(
    'server.py', 'lyrics.py', 'songtools.py', 'fastsearch.py', 'librarytools.py', 'musiccompat.py', 'requirements.txt', 'Dockerfile', '.dockerignore',
    '.env.example', '.gitignore', '.gitattributes',
    'web/index.html', 'web/styles.css', 'web/lyrics.css', 'web/app.js', 'web/memory.js', 'web/queue.js', 'web/favicon.svg',
    'deploy/entrypoint.sh', 'deploy/package.ps1', 'start.ps1',
    'tests/test_server.py', 'tests/test_songtools.py', 'tests/test_fastsearch.py', 'tests/test_musiccompat.py', 'tests/test_library.py', 'tests/test_bulk_delete.py', 'tests/test_memory.cjs', 'tests/test_queue.cjs', 'tests/test_bulk.cjs',
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
