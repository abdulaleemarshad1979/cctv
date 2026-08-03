import sys
import os
from reportlab.lib.pagesizes import letter
from reportlab.lib import colors
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, KeepTogether, HRFlowable
)
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
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
        
        # Header (Only if > 1 page)
        if self._pageNumber > 1:
            self.setFont("Helvetica-Bold", 8)
            self.setFillColor(colors.HexColor("#4A5568"))
            self.drawString(28, letter[1] - 20, "SECURITY & TECHNICAL EXPOSURE REPORT")
            self.setFont("Helvetica", 8)
            self.drawRightString(letter[0] - 28, letter[1] - 20, "CONFIDENTIAL")
            self.setStrokeColor(colors.HexColor("#CBD5E0"))
            self.setLineWidth(0.75)
            self.line(28, letter[1] - 24, letter[0] - 28, letter[1] - 24)

        # Footer
        self.setFont("Helvetica", 8)
        self.setFillColor(colors.HexColor("#64748B"))
        self.drawString(28, 18, "Target Document: SYSTEM_GUIDE-1.pdf  |  Proprietary Risk Assessment")
        
        page_text = f"Page {self._pageNumber} of {page_count}"
        self.drawRightString(letter[0] - 28, 18, page_text)
        
        self.setStrokeColor(colors.HexColor("#CBD5E0"))
        self.setLineWidth(0.75)
        self.line(28, 28, letter[0] - 28, 28)

        self.restoreState()


def create_pdf(filename="SYSTEM_GUIDE-1_Exposure_Report.pdf"):
    # Printable width: 612 - 56 = 556pt
    doc = SimpleDocTemplate(
        filename,
        pagesize=letter,
        leftMargin=28,
        rightMargin=28,
        topMargin=28,
        bottomMargin=32
    )

    styles = getSampleStyleSheet()
    
    COLOR_PRIMARY = colors.HexColor("#0F172A")     # Dark Slate
    COLOR_CRITICAL = colors.HexColor("#DC2626")    # Red 600
    COLOR_AMBER = colors.HexColor("#D97706")       # Amber 600
    COLOR_TEXT = colors.HexColor("#1E293B")        # Dark text
    COLOR_LIGHT_BG = colors.HexColor("#F8FAFC")    # Slate 50
    COLOR_CARD_BORDER = colors.HexColor("#CBD5E0")

    title_style = ParagraphStyle(
        'DocTitle',
        parent=styles['Normal'],
        fontName='Helvetica-Bold',
        fontSize=18,
        leading=22,
        textColor=COLOR_CRITICAL,
        spaceAfter=2
    )

    subtitle_style = ParagraphStyle(
        'DocSubtitle',
        parent=styles['Normal'],
        fontName='Helvetica-Bold',
        fontSize=10,
        leading=13,
        textColor=COLOR_PRIMARY,
        spaceAfter=0
    )

    h1_style = ParagraphStyle(
        'SectionH1',
        parent=styles['Normal'],
        fontName='Helvetica-Bold',
        fontSize=11,
        leading=14,
        textColor=COLOR_PRIMARY,
        spaceBefore=8,
        spaceAfter=4
    )

    body_style = ParagraphStyle(
        'BodyDark',
        parent=styles['Normal'],
        fontName='Helvetica',
        fontSize=9,
        leading=13,
        textColor=COLOR_TEXT,
        spaceAfter=4
    )

    bullet_style = ParagraphStyle(
        'BulletDark',
        parent=styles['Normal'],
        fontName='Helvetica',
        fontSize=8.8,
        leading=12.5,
        textColor=COLOR_TEXT,
        leftIndent=10,
        spaceAfter=3
    )

    callout_txt_style = ParagraphStyle(
        'CalloutTxt',
        parent=styles['Normal'],
        fontName='Helvetica',
        fontSize=9,
        leading=13,
        textColor=COLOR_TEXT
    )

    q_style = ParagraphStyle(
        'QStyle',
        parent=styles['Normal'],
        fontName='Helvetica-Bold',
        fontSize=10,
        leading=13,
        textColor=COLOR_PRIMARY
    )

    a_style = ParagraphStyle(
        'AStyle',
        parent=styles['Normal'],
        fontName='Helvetica-Bold',
        fontSize=10,
        leading=13,
        textColor=COLOR_CRITICAL
    )

    story = []

    # Title Banner Block
    banner_data = [
        [Paragraph("Information that should NOT be shared", title_style)],
        [Paragraph("SECURITY & TECHNICAL EXPOSURE ANALYSIS OF <code>SYSTEM_GUIDE-1.pdf</code>", subtitle_style)]
    ]
    banner_table = Table(banner_data, colWidths=[556])
    banner_table.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,-1), colors.HexColor("#FEF2F2")),
        ('BOX', (0,0), (-1,-1), 1.25, colors.HexColor("#FCA5A5")),
        ('PADDING', (0,0), (-1,-1), 8),
        ('BOTTOMPADDING', (0,-1), (-1,-1), 6),
    ]))
    story.append(banner_table)
    story.append(Spacer(1, 8))

    # Intro Context Paragraph
    story.append(Paragraph(
        "The guide reveals many technical implementation details that competitors or engineers could use to recreate a similar system:",
        body_style
    ))
    story.append(Spacer(1, 4))

    # Exposed Technical Details List
    exposed_details = [
        "The exact AI models you use (DM-Count, CSRNet, YOLO11, LSTM).",
        "The motion analysis algorithm (Farneback Optical Flow).",
        "The altitude correction equation.",
        "The turbulence metric formula.",
        "The composite risk score formula and weighting approach.",
        "The streaming architecture (MediaMTX, RTMP, WebRTC).",
        "Internal file names (config.py, launch.py, swarm_infer.py, etc.).",
        "Training commands and system setup instructions."
    ]

    for detail in exposed_details:
        story.append(Paragraph(f"• {detail}", bullet_style))

    story.append(Spacer(1, 10))

    # Target Document Badge
    doc_badge_data = [
        [
            Paragraph("<b>Target Evaluated Document:</b> <code>SYSTEM_GUIDE-1.pdf</code>", body_style),
            Paragraph("<b>Risk Status:</b> <font color='#DC2626'>HIGH PROPRIETARY EXPOSURE</font>", body_style)
        ]
    ]
    doc_badge_table = Table(doc_badge_data, colWidths=[310, 246])
    doc_badge_table.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,-1), colors.HexColor("#F1F5F9")),
        ('BOX', (0,0), (-1,-1), 0.75, colors.HexColor("#CBD5E0")),
        ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
        ('PADDING', (0,0), (-1,-1), 5),
        ('LEFTPADDING', (0,0), (-1,-1), 8),
    ]))
    story.append(doc_badge_table)
    story.append(Spacer(1, 12))

    # Q&A Block: Can someone build a similar system from this?
    qa_data = [
        [Paragraph("Can someone build a similar system from this?", q_style)],
        [Paragraph("Yes, to some extent.", a_style)],
        [Paragraph(
            "This document doesn't expose your actual source code or trained models, but it provides enough information for an experienced AI team to understand:",
            callout_txt_style
        )],
        [Paragraph("• <b>which technologies to use</b>,", bullet_style)],
        [Paragraph("• <b>how the pipeline is organized</b>,", bullet_style)],
        [Paragraph("• <b>the formulas involved</b>,", bullet_style)],
        [Paragraph("• <b>and how the components interact</b>.", bullet_style)],
        [Paragraph(
            "<b>Conclusion:</b> That significantly reduces the effort required to build a competing solution.",
            ParagraphStyle('ConcStyle', parent=styles['Normal'], fontName='Helvetica-Bold', fontSize=9, textColor=COLOR_AMBER)
        )]
    ]

    qa_table = Table(qa_data, colWidths=[556])
    qa_table.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,-1), COLOR_LIGHT_BG),
        ('BOX', (0,0), (-1,-1), 1, COLOR_CARD_BORDER),
        ('LINEBELOW', (0,0), (-1,0), 0.75, colors.HexColor("#E2E8F0")),
        ('PADDING', (0,0), (-1,-1), 6),
        ('LEFTPADDING', (0,0), (-1,-1), 10),
        ('RIGHTPADDING', (0,0), (-1,-1), 10),
    ]))
    
    story.append(KeepTogether([qa_table]))
    story.append(Spacer(1, 12))

    # Recommendation Summary
    story.append(Paragraph("Recommended Remediation Strategy", h1_style))
    story.append(HRFlowable(width="100%", thickness=0.75, color=COLOR_PRIMARY, spaceBefore=1, spaceAfter=6))

    remedies = [
        "<b>Redact Formulas:</b> Omit exact math equations (altitude correction, turbulence variance) from external copies.",
        "<b>Abstract Model Names:</b> Use generic technical labels (e.g., 'Density Estimation CNN') instead of specific model architectures.",
        "<b>Remove Internal Paths:</b> Strip internal Python script filenames, pathing structures, and CLI setup commands.",
        "<b>Enforce 2-Tier Access:</b> Maintain a high-level Client Guide (Tier 1) separate from Internal NDA Engineering Specifications (Tier 2)."
    ]

    for remedy in remedies:
        story.append(Paragraph(f"• {remedy}", bullet_style))

    # Build PDF
    doc.build(story, canvasmaker=NumberedCanvas)
    
    # Also save to root standard names
    import shutil
    shutil.copy(filename, "SYSTEM_GUIDE_EXPOSURE.pdf")
    shutil.copy(filename, "SYSTEM_GUIDE-1.pdf_Report.pdf")
    print(f"Successfully generated {filename}")

if __name__ == "__main__":
    create_pdf()
