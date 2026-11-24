param(
    [Parameter(Mandatory)] [string] $Python,
    [string] $Config = (Join-Path (Split-Path $PSScriptRoot -Parent) 'config.json'),
    [string] $TaskName = 'Kantkod-TransparentVacancy',
    [switch] $Preview
)
$ErrorActionPreference = 'Stop'
$project = Split-Path $PSScriptRoot -Parent
$executable = (Get-Command -Name $Python -CommandType Application).Source
$configPath = (Resolve-Path -LiteralPath $Config).Path
$settings = Get-Content -LiteralPath $configPath -Raw -Encoding UTF8 | ConvertFrom-Json
$time = [DateTime]::ParseExact($settings.schedule_time, 'HH:mm', [Globalization.CultureInfo]::InvariantCulture)
$arguments = '-m kantkod live --config "' + $configPath + '"'

if ($Preview) {
    [pscustomobject]@{
        TaskName = $TaskName
        Execute = $executable
        Arguments = $arguments
        WorkingDirectory = $project
        DailyAt = $time.ToString('HH:mm')
    }
    return
}

$action = New-ScheduledTaskAction -Execute $executable -Argument $arguments -WorkingDirectory $project
$trigger = New-ScheduledTaskTrigger -Daily -At $time
$options = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 30)
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $options
