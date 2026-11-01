//! PDF pages through Windows' own renderer (Windows.Data.Pdf), so the executable ships no PDF engine.

use gpui::{Image, ImageFormat};
use std::sync::{Arc, mpsc};
use windows::{
    Data::Pdf::{PdfDocument, PdfPageRenderOptions},
    Storage::Streams::{DataReader, DataWriter, InMemoryRandomAccessStream},
    Win32::System::WinRT::{RO_INIT_MULTITHREADED, RoInitialize},
};

pub struct Page {
    pub image: Arc<Image>,
    /// The bitmap's size. GPUI samples without mipmaps, so a page stays sharp only when it is
    /// drawn at exactly this size.
    pub pixels: (u32, u32),
}

/// The bitmap width that fills a page drawn this wide on this screen.
pub fn target_width(logical_width: f32, scale: f32) -> u32 {
    ((logical_width * scale).round() as u32).clamp(200, 6000)
}

/// The logical size that spans exactly `target` device pixels. GPUI rounds an image's edges to whole
/// pixels separately, so a whole-pixel size keeps its width wherever it starts, and the bitmap is
/// never stretched by a pixel. A bitmap from before a resize scales until its replacement arrives.
pub fn display_size(pixels: (u32, u32), target: u32, scale: f32) -> (f32, f32) {
    let height = pixels.1 as f32 * target as f32 / pixels.0.max(1) as f32;
    (target as f32 / scale, height / scale)
}

fn render(bytes: &[u8], width: u32, send: &mpsc::Sender<Result<Page, String>>) -> windows::core::Result<()> {
    unsafe { RoInitialize(RO_INIT_MULTITHREADED).ok() };
    let input = InMemoryRandomAccessStream::new()?;
    let writer = DataWriter::CreateDataWriter(&input.GetOutputStreamAt(0)?)?;
    writer.WriteBytes(bytes)?;
    writer.StoreAsync()?.join()?;
    writer.FlushAsync()?.join()?;
    writer.DetachStream()?;
    input.Seek(0)?;
    let document = PdfDocument::LoadFromStreamAsync(&input)?.join()?;
    for index in 0..document.PageCount()? {
        let page = document.GetPage(index)?;
        let page_size = page.Size()?;
        let options = PdfPageRenderOptions::new()?;
        let height = (width as f32 * page_size.Height / page_size.Width).round().max(1.) as u32;
        options.SetDestinationWidth(width)?;
        options.SetDestinationHeight(height)?;
        let output = InMemoryRandomAccessStream::new()?;
        page.RenderWithOptionsToStreamAsync(&output, &options)?.join()?;
        let length = output.Size()? as u32;
        let reader = DataReader::CreateDataReader(&output.GetInputStreamAt(0)?)?;
        reader.LoadAsync(length)?.join()?;
        let mut png = vec![0u8; length as usize];
        reader.ReadBytes(&mut png)?;
        let image = Arc::new(Image::from_bytes(ImageFormat::Png, png));
        let sent = send.send(Ok(Page {
            image,
            pixels: (width, height),
        }));
        if sent.is_err() {
            break;
        }
    }
    Ok(())
}

/// Pages arrive one at a time, in order, so a long paper shows its first page straight away.
pub fn render_pages(bytes: Arc<Vec<u8>>, width: u32) -> mpsc::Receiver<Result<Page, String>> {
    let (send, receive) = mpsc::channel();
    std::thread::spawn(move || {
        if let Err(error) = render(&bytes, width, &send) {
            let _ = send.send(Err(error.message()));
        }
    });
    receive
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_page_lands_on_whole_device_pixels() {
        for scale in [1., 1.25, 1.5, 1.75, 2., 2.25] {
            for target in [200, 480, 481, 719, 1800, 3333] {
                let (width, height) = display_size((target, target * 13 / 10), target, scale);
                // What GPUI does to an image's edges when it paints it, from an uneven origin.
                for origin in [0., 13.37, 512.2, 701.81] {
                    let span = |length: f32| ((origin + length) * scale).round() - (origin * scale).round();
                    assert_eq!(span(width) as u32, target, "{scale} {target} {origin}");
                    assert_eq!(span(height) as u32, target * 13 / 10, "{scale} {target} {origin}");
                }
            }
        }
    }

    #[test]
    fn the_bitmap_follows_the_screen_and_stays_bounded() {
        assert_eq!(target_width(320., 1.5), 480);
        assert_eq!(target_width(320., 1.), 320);
        assert_eq!(target_width(10., 1.), 200);
        assert_eq!(target_width(9000., 2.), 6000);
    }
}
