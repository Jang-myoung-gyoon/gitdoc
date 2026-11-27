#[cfg(target_os = "macos")]
pub fn print(path: &str, title: &str, _: &str, _: &eframe::Frame) -> Result<bool, String> {
    use objc2::{AnyThread, MainThreadMarker};
    use objc2_app_kit::NSPrintInfo;
    use objc2_foundation::{NSString, NSURL};
    use objc2_pdf_kit::{PDFDocument, PDFPrintScalingMode};

    let main = MainThreadMarker::new().ok_or("인쇄 대화상자를 열 수 없습니다.")?;
    let url = NSURL::fileURLWithPath(&NSString::from_str(path));
    // The URL is a local cached PDF. AppKit's modal operation runs on the window thread.
    let document = unsafe { PDFDocument::initWithURL(PDFDocument::alloc(), &url) }
        .ok_or("인쇄할 PDF를 열 수 없습니다.")?;
    if unsafe { document.isLocked() || !document.allowsPrinting() } {
        return Err("이 PDF의 인쇄 권한을 확인하세요.".into());
    }
    let info = NSPrintInfo::sharedPrintInfo();
    let operation = unsafe {
        document.printOperationForPrintInfo_scalingMode_autoRotate(
            Some(&info),
            PDFPrintScalingMode::PageScaleDownToFit,
            true,
            main,
        )
    }
    .ok_or("인쇄 대화상자를 열 수 없습니다.")?;
    operation.setJobTitle(Some(&NSString::from_str(title)));
    Ok(operation.runOperation())
}

#[cfg(windows)]
pub use win32::print;

#[cfg(windows)]
mod win32 {
    use pdfium_render::prelude::PdfiumLibraryBindingsAccessor;
    use raw_window_handle::{HasWindowHandle, RawWindowHandle};
    use std::{mem::size_of, path::Path, ptr::null};
    use windows::Win32::Graphics::Gdi::HDC;
    use windows_sys::Win32::{
        Foundation::{GlobalFree, HWND},
        Graphics::Gdi::{DeleteDC, GetDeviceCaps, HORZRES, LOGPIXELSX, LOGPIXELSY, VERTRES},
        Storage::Xps::{AbortDoc, DOCINFOW, EndDoc, EndPage, StartDocW, StartPage},
        UI::Controls::Dialogs::{
            CommDlgExtendedError, PD_NOSELECTION, PD_PAGENUMS, PD_PRINTTOFILE, PD_RETURNDC,
            PD_USEDEVMODECOPIESANDCOLLATE, PRINTDLGW, PrintDlgW,
        },
    };

    struct Dialog(PRINTDLGW);

    impl Drop for Dialog {
        fn drop(&mut self) {
            // The Win32 dialog transfers ownership of these handles to the caller.
            unsafe {
                if !self.0.hDC.is_null() {
                    DeleteDC(self.0.hDC);
                }
                if !self.0.hDevMode.is_null() {
                    GlobalFree(self.0.hDevMode);
                }
                if !self.0.hDevNames.is_null() {
                    GlobalFree(self.0.hDevNames);
                }
            }
        }
    }

    pub fn print(
        path: &str,
        title: &str,
        directory: &str,
        frame: &eframe::Frame,
    ) -> Result<bool, String> {
        let handle = frame.window_handle().map_err(|error| error.to_string())?;
        let RawWindowHandle::Win32(window) = handle.as_raw() else {
            return Err("Windows 인쇄 창을 열 수 없습니다.".into());
        };
        let pdfium = crate::pages::load_pdfium(directory)?;
        let document = pdfium
            .load_pdf_from_file(Path::new(path), None)
            .map_err(|_| "인쇄할 PDF를 열 수 없습니다.")?;
        let total = u16::try_from(document.pages().len())
            .ok()
            .filter(|&total| total > 0)
            .ok_or("인쇄할 페이지 수를 확인하세요.")?;
        let mut dialog = Dialog(PRINTDLGW {
            lStructSize: size_of::<PRINTDLGW>() as u32,
            hwndOwner: window.hwnd.get() as HWND,
            Flags: PD_RETURNDC | PD_NOSELECTION | PD_USEDEVMODECOPIESANDCOLLATE,
            nFromPage: 1,
            nToPage: total,
            nMinPage: 1,
            nMaxPage: total,
            nCopies: 1,
            ..Default::default()
        });
        // Win32 calls use the live window, dialog buffers and printer DC until we drop Dialog.
        unsafe {
            if PrintDlgW(&mut dialog.0) == 0 {
                let error = CommDlgExtendedError();
                return if error == 0 {
                    Ok(false)
                } else {
                    Err(format!("인쇄 대화상자를 열 수 없습니다. ({error:#x})"))
                };
            }
            let dc = dialog.0.hDC;
            let (width, height) = (
                GetDeviceCaps(dc, HORZRES as i32),
                GetDeviceCaps(dc, VERTRES as i32),
            );
            let (dpi_x, dpi_y) = (
                GetDeviceCaps(dc, LOGPIXELSX as i32),
                GetDeviceCaps(dc, LOGPIXELSY as i32),
            );
            if [width, height, dpi_x, dpi_y]
                .into_iter()
                .any(|value| value <= 0)
            {
                return Err("프린터 용지 크기를 읽을 수 없습니다.".into());
            }
            let title: Vec<u16> = title.replace('\0', "").encode_utf16().chain([0]).collect();
            let file: Vec<u16> = "FILE:".encode_utf16().chain([0]).collect();
            let info = DOCINFOW {
                cbSize: size_of::<DOCINFOW>() as i32,
                lpszDocName: title.as_ptr(),
                lpszOutput: if dialog.0.Flags & PD_PRINTTOFILE != 0 {
                    file.as_ptr()
                } else {
                    null()
                },
                ..Default::default()
            };
            let (first, last) = if dialog.0.Flags & PD_PAGENUMS != 0 {
                (
                    dialog.0.nFromPage.clamp(1, total),
                    dialog.0.nToPage.clamp(1, total),
                )
            } else {
                (1, total)
            };
            if first > last || StartDocW(dc, &info) <= 0 {
                return Err("인쇄 작업을 시작할 수 없습니다.".into());
            }
            let bindings = pdfium.bindings();
            let print_pages = || -> Result<(), String> {
                for number in first..=last {
                    let page = document
                        .pages()
                        .get(i32::from(number) - 1)
                        .map_err(|_| format!("{number}쪽을 읽을 수 없습니다."))?;
                    let device_width = f64::from(page.width().value) * f64::from(dpi_x);
                    let device_height = f64::from(page.height().value) * f64::from(dpi_y);
                    if !device_width.is_finite()
                        || !device_height.is_finite()
                        || device_width <= 0.0
                        || device_height <= 0.0
                    {
                        return Err(format!("{number}쪽의 크기를 읽을 수 없습니다."));
                    }
                    let scale =
                        (f64::from(width) / device_width).min(f64::from(height) / device_height);
                    let page_width = (device_width * scale).round() as i32;
                    let page_height = (device_height * scale).round() as i32;
                    if StartPage(dc) <= 0 {
                        return Err(format!("{number}쪽을 인쇄할 수 없습니다."));
                    }
                    const FPDF_ANNOT: i32 = 0x01;
                    const FPDF_PRINTING: i32 = 0x800;
                    bindings.FPDF_RenderPage(
                        HDC(dc),
                        bindings.get_handle_from_page(&page),
                        (width - page_width) / 2,
                        (height - page_height) / 2,
                        page_width,
                        page_height,
                        0,
                        FPDF_ANNOT | FPDF_PRINTING,
                    );
                    if EndPage(dc) <= 0 {
                        return Err(format!("{number}쪽을 인쇄할 수 없습니다."));
                    }
                }
                Ok(())
            };
            if let Err(error) = print_pages() {
                AbortDoc(dc);
                return Err(error);
            }
            if EndDoc(dc) <= 0 {
                return Err("인쇄 작업을 완료하지 못했습니다.".into());
            }
            Ok(true)
        }
    }
}

#[cfg(not(any(target_os = "macos", windows)))]
pub fn print(_: &str, _: &str, _: &str, _: &eframe::Frame) -> Result<bool, String> {
    Err("이 운영체제에서는 인쇄를 지원하지 않습니다.".into())
}
