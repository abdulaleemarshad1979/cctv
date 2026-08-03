import os
import sys

sys.stdout.reconfigure(encoding='utf-8')

def convert_pptx_to_pdf(input_path, output_path):
    abs_input = os.path.abspath(input_path)
    abs_output = os.path.abspath(output_path)
    
    print(f"Converting {abs_input} to {abs_output}...")
    
    try:
        import win32com.client
        powerpoint = win32com.client.Dispatch("PowerPoint.Application")
        powerpoint.Visible = 1
        
        deck = powerpoint.Presentations.Open(abs_input, False, False, False)
        deck.SaveAs(abs_output, 32) # 32 represents ppSaveAsPDF
        deck.Close()
        powerpoint.Quit()
        print("PDF conversion via PowerPoint COM API succeeded!")
        return True
    except Exception as e:
        print(f"PowerPoint COM API conversion not available/failed: {e}")
        return False

if __name__ == "__main__":
    pptx_file = os.path.join("PPT", "SIH_2026_Pushkaralu_Crowd_Monitor.pptx")
    pdf_file = os.path.join("PPT", "SIH_2026_Pushkaralu_Crowd_Monitor.pdf")
    if os.path.exists(pptx_file):
        convert_pptx_to_pdf(pptx_file, pdf_file)
    else:
        print("Input PPTX file does not exist yet.")
