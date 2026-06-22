param(
    [Parameter(Mandatory = $true)][string]$ManifestPath,
    [string]$PrinterName = "",
    [switch]$NoDialog,
    [switch]$ValidateOnly
)

$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing

$source = @'
using System;
using System.Drawing;
using System.Drawing.Printing;
using System.Windows.Forms;

public static class SpecimenWindowsPrinter
{
    public static string Run(string[] paths, double[] widths, double[] heights,
                             string documentName, string printerName, bool showDialog)
    {
        if (paths == null || paths.Length == 0) return "CANCELLED";
        using (PrintDocument doc = new PrintDocument())
        {
            doc.DocumentName = String.IsNullOrWhiteSpace(documentName) ? "Specimen labels" : documentName;
            if (!String.IsNullOrWhiteSpace(printerName))
                doc.PrinterSettings.PrinterName = printerName;
            if (!doc.PrinterSettings.IsValid && !showDialog)
                throw new InvalidOperationException("The selected printer is unavailable: " + printerName);

            int first = 0;
            int last = paths.Length - 1;
            int current = 0;

            using (PrintDialog dialog = new PrintDialog())
            {
                dialog.Document = doc;
                dialog.UseEXDialog = true;
                dialog.AllowCurrentPage = false;
                dialog.AllowSelection = false;
                dialog.AllowSomePages = paths.Length > 1;
                dialog.PrinterSettings.MinimumPage = 1;
                dialog.PrinterSettings.MaximumPage = paths.Length;
                dialog.PrinterSettings.FromPage = 1;
                dialog.PrinterSettings.ToPage = paths.Length;

                if (showDialog && dialog.ShowDialog() != DialogResult.OK)
                    return "CANCELLED";

                if (dialog.PrinterSettings.PrintRange == PrintRange.SomePages)
                {
                    first = Math.Max(0, dialog.PrinterSettings.FromPage - 1);
                    last = Math.Min(paths.Length - 1, dialog.PrinterSettings.ToPage - 1);
                }
                current = first;

                doc.QueryPageSettings += delegate(object sender, QueryPageSettingsEventArgs e)
                {
                    int w = Math.Max(1, (int)Math.Round(widths[current] / 25.4 * 100.0));
                    int h = Math.Max(1, (int)Math.Round(heights[current] / 25.4 * 100.0));
                    e.PageSettings.Landscape = false;
                    e.PageSettings.Margins = new Margins(0, 0, 0, 0);
                    e.PageSettings.PaperSize = new PaperSize("Custom label page", w, h);
                };

                doc.PrintPage += delegate(object sender, PrintPageEventArgs e)
                {
                    using (Image image = Image.FromFile(paths[current]))
                    {
                        e.Graphics.DrawImage(image, e.PageBounds);
                    }
                    current++;
                    e.HasMorePages = current <= last;
                };

                doc.Print();
                return "PRINTED|" + doc.PrinterSettings.PrinterName;
            }
        }
    }
}
'@

Add-Type -TypeDefinition $source -ReferencedAssemblies System.Drawing,System.Windows.Forms
if ($ValidateOnly) {
    Write-Output "VALID"
    exit 0
}
$manifest = Get-Content -LiteralPath $ManifestPath -Raw -Encoding UTF8 | ConvertFrom-Json
$paths = @($manifest.pages | ForEach-Object { [string]$_.path })
$widths = @($manifest.pages | ForEach-Object { [double]$_.width_mm })
$heights = @($manifest.pages | ForEach-Object { [double]$_.height_mm })

$result = [SpecimenWindowsPrinter]::Run(
    [string[]]$paths,
    [double[]]$widths,
    [double[]]$heights,
    [string]$manifest.document_name,
    $PrinterName,
    (-not $NoDialog)
)
Write-Output $result
