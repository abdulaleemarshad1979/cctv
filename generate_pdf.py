import os
import re
import sys
from reportlab.lib.pagesizes import letter
from reportlab.lib import colors
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Preformatted, HRFlowable, KeepTogether
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.pdfgen import canvas

class NumberedCanvas(canvas.Canvas):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._saved_page_states = []

    def showPage(self):
        self._saved_page_states.append(dict(self.__dict__))
        self._startPage()

    def save(self):
        num_pages = len(self._saved_page_states)
        for state in self._saved_page_states:
            self.__dict__.update(state)
            self.draw_page_decorations(num_pages)
            super().showPage()
        super().save()

    def draw_page_decorations(self, page_count):
        self.saveState()
        
        # Color palette
        teal = colors.HexColor("#0D5C75")
        grey = colors.HexColor("#666666")
        light_grey = colors.HexColor("#EAEAEA")

        # Top running header (suppressed on page 1)
        if self._pageNumber > 1:
            self.setFont("Helvetica-Bold", 8)
            self.setFillColor(teal)
            self.drawString(54, 750, "PUSHKARALU DRONE CROWD MONITOR & RISK ENGINE")
            self.setFont("Helvetica", 8)
            self.setFillColor(grey)
            self.drawRightString(612 - 54, 750, "SYSTEM GUIDE")
            
            # Header line
            self.setStrokeColor(light_grey)
            self.setLineWidth(0.5)
            self.line(54, 742, 612 - 54, 742)

        # Bottom running footer (on all pages)
        self.setFont("Helvetica", 9)
        self.setFillColor(grey)
        self.drawString(54, 36, "Operational Concept Document — Confidential")
        self.drawRightString(612 - 54, 36, f"Page {self._pageNumber} of {page_count}")
        
        # Footer line
        self.setStrokeColor(light_grey)
        self.setLineWidth(0.5)
        self.line(54, 48, 612 - 54, 48)
        
        self.restoreState()

def parse_markdown_to_flowables(filepath, styles):
    with open(filepath, "r", encoding="utf-8") as f:
        content = f.read()

    flowables = []
    
    # Split content by code blocks and normal text
    parts = re.split(r"(```.*?```)", content, flags=re.DOTALL)
    
    for part in parts:
        part = part.strip()
        if not part:
            continue
            
        if part.startswith("```"):
            # It's an ASCII diagram or code block
            lines = part.strip("`").strip("\n").split("\n")
            # Strip language if any (e.g. ```text)
            if lines and not lines[0].startswith(" ") and len(lines[0]) < 10 and not any(c in lines[0] for c in "|+-"):
                lines = lines[1:]
            text_content = "\n".join(lines)
            
            # Create a box for the diagram
            flowables.append(Spacer(1, 10))
            flowables.append(Preformatted(text_content, styles["DiagramStyle"]))
            flowables.append(Spacer(1, 10))
        else:
            # Parse line by line
            lines = part.split("\n")
            in_list = False
            for line in lines:
                line_str = line.strip()
                if not line_str:
                    continue
                
                # Inline formatting helper: convert **text** to <b>text</b>
                def inline_format(text):
                    text = re.sub(r"\*\*(.*?)\*\*", r"<b>\1</b>", text)
                    text = re.sub(r"\*(.*?)\*", r"<i>\1</i>", text)
                    text = re.sub(r"\[(.*?)\]\(.*?\)", r"\1", text)  # remove markdown links, keep text
                    # Escaping XML entities for ReportLab paragraphs
                    text = text.replace("&", "&amp;")
                    # But restore <b>, <i>, <bullet> tags
                    text = text.replace("&amp;lt;b&amp;gt;", "<b>").replace("&amp;lt;/b&amp;gt;", "</b>")
                    text = text.replace("&amp;lt;i&amp;gt;", "<i>").replace("&amp;lt;/i&amp;gt;", "</i>")
                    return text

                # Check horizontal rules
                if line_str == "---":
                    flowables.append(Spacer(1, 10))
                    flowables.append(HRFlowable(width="100%", thickness=0.8, color=colors.HexColor("#D1D5DB"), spaceBefore=5, spaceAfter=15))
                    continue

                # Check headers
                if line_str.startswith("# "):
                    title_text = inline_format(line_str[2:])
                    flowables.append(Spacer(1, 20))
                    flowables.append(Paragraph(title_text, styles["DocTitle"]))
                    flowables.append(Spacer(1, 15))
                elif line_str.startswith("## "):
                    h1_text = inline_format(line_str[3:])
                    flowables.append(Spacer(1, 15))
                    flowables.append(Paragraph(h1_text, styles["Heading1"]))
                    flowables.append(Spacer(1, 10))
                elif line_str.startswith("### "):
                    h2_text = inline_format(line_str[4:])
                    flowables.append(Spacer(1, 12))
                    flowables.append(Paragraph(h2_text, styles["Heading2"]))
                    flowables.append(Spacer(1, 6))
                elif line_str.startswith("* ") or line_str.startswith("- "):
                    # List item
                    list_text = inline_format(line_str[2:])
                    flowables.append(Paragraph(f"<bullet>&bull;</bullet>{list_text}", styles["BulletText"]))
                else:
                    # Normal paragraph
                    para_text = inline_format(line_str)
                    flowables.append(Paragraph(para_text, styles["BodyText"]))
                    flowables.append(Spacer(1, 8))

    return flowables

def main():
    workspace_dir = os.path.dirname(os.path.abspath(__file__))
    md_path = os.path.join(workspace_dir, "SYSTEM_GUIDE.md")
    pdf_path = os.path.join(workspace_dir, "SYSTEM_GUIDE.pdf")

    if not os.path.exists(md_path):
        print(f"Error: {md_path} not found.")
        sys.exit(1)

    print(f"Reading markdown from {md_path}...")

    # Define color scheme
    primary_color = colors.HexColor("#0D5C75")    # Deep Teal
    text_color = colors.HexColor("#2C3E50")       # Dark Slate
    bg_diagram = colors.HexColor("#F8F9FA")       # Off-White
    border_diagram = colors.HexColor("#E2E8F0")   # Light Gray Border

    # Styles
    styles = getSampleStyleSheet()

    # Custom styles
    styles.add(ParagraphStyle(
        name="DocTitle",
        fontName="Helvetica-Bold",
        fontSize=22,
        leading=28,
        textColor=primary_color,
        spaceAfter=15,
        alignment=0 # Left aligned
    ))

    # Overwrite default Heading1, Heading2, BodyText, Bullet
    styles["Heading1"].fontName = "Helvetica-Bold"
    styles["Heading1"].fontSize = 14
    styles["Heading1"].leading = 18
    styles["Heading1"].textColor = primary_color
    styles["Heading1"].spaceBefore = 18
    styles["Heading1"].spaceAfter = 10
    styles["Heading1"].keepWithNext = True

    styles["Heading2"].fontName = "Helvetica-Bold"
    styles["Heading2"].fontSize = 11
    styles["Heading2"].leading = 15
    styles["Heading2"].textColor = text_color
    styles["Heading2"].spaceBefore = 12
    styles["Heading2"].spaceAfter = 6
    styles["Heading2"].keepWithNext = True

    styles["BodyText"].fontName = "Helvetica"
    styles["BodyText"].fontSize = 10
    styles["BodyText"].leading = 14
    styles["BodyText"].textColor = text_color
    styles["BodyText"].spaceAfter = 8

    styles.add(ParagraphStyle(
        name="BulletText",
        parent=styles["BodyText"],
        leftIndent=20,
        firstLineIndent=-10,
        spaceAfter=6
    ))

    styles.add(ParagraphStyle(
        name="DiagramStyle",
        fontName="Courier",
        fontSize=7.5,
        leading=9.5,
        textColor=colors.HexColor("#1A202C"),
        backColor=bg_diagram,
        borderColor=border_diagram,
        borderWidth=0.5,
        borderPadding=10,
        spaceAfter=10
    ))

    doc = SimpleDocTemplate(
        pdf_path,
        pagesize=letter,
        leftMargin=54, # 0.75 inch
        rightMargin=54,
        topMargin=72,  # 1.0 inch
        bottomMargin=72
    )

    flowables = parse_markdown_to_flowables(md_path, styles)
    
    print(f"Generating PDF at {pdf_path}...")
    doc.build(flowables, canvasmaker=NumberedCanvas)
    print("PDF Generation complete!")

if __name__ == "__main__":
    main()
