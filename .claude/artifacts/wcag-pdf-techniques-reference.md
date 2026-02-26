# WCAG 2.1 PDF Techniques Reference — Complete Extraction
> Source: https://www.w3.org/WAI/WCAG21/Techniques/
> Extracted: 2026-02-25
> Techniques: PDF1 through PDF23 (all 23 techniques)
> Plus: Relevant failure techniques (F-series)

---

## Summary Table: All 23 PDF Techniques

| ID | Title | WCAG Criteria | Type |
|----|-------|---------------|------|
| PDF1 | Applying text alternatives to images with the Alt entry | 1.1.1 | Sufficient |
| PDF2 | Creating bookmarks in PDF documents | 2.4.5 | Advisory |
| PDF3 | Ensuring correct tab and reading order | 1.3.2, 2.1.1, 2.4.3 | Sufficient |
| PDF4 | Hiding decorative images with the Artifact tag | 1.1.1 | Sufficient |
| PDF5 | Indicating required form controls in PDF forms | 3.3.1, 3.3.2 | Sufficient |
| PDF6 | Using table elements for table markup | 1.3.1 | Sufficient |
| PDF7 | Performing OCR on a scanned PDF document | 1.4.5, 1.4.9 | Sufficient |
| PDF8 | Providing definitions for abbreviations via an E entry | 3.1.4 | Sufficient |
| PDF9 | Providing headings by marking content with heading tags | 1.3.1, 2.4.1 | Sufficient |
| PDF10 | Providing labels for interactive form controls | 1.3.1, 3.3.2, 4.1.2 | Sufficient |
| PDF11 | Providing links and link text using Link annotation | 1.3.1, 2.1.1, 2.4.4, 2.4.9 | Sufficient |
| PDF12 | Providing name, role, value information for form fields | 1.3.1, 4.1.2 | Sufficient |
| PDF13 | Providing replacement text using /Alt entry for links | 2.4.4, 2.4.9 | Sufficient |
| PDF14 | Providing running headers and footers | 2.4.8, 3.2.3 | Advisory |
| PDF15 | Providing submit buttons with submit-form action | 3.2.2 | Sufficient |
| PDF16 | Setting the default language using /Lang entry | 3.1.1 | Sufficient |
| PDF17 | Specifying consistent page numbering | 1.3.1, 2.4.8, 3.2.3 | Sufficient/Advisory |
| PDF18 | Specifying the document title using Title entry | 2.4.2 | Sufficient |
| PDF19 | Specifying language for a passage or phrase with Lang | 3.1.1, 3.1.2 | Sufficient |
| PDF20 | Using Adobe Acrobat Pro's Table Editor to repair tables | 1.3.1 | Sufficient |
| PDF21 | Using List tags for lists in PDF documents | 1.3.1 | Sufficient |
| PDF22 | Indicating when user input falls outside required format | 3.3.1, 3.3.3 | Sufficient |
| PDF23 | Providing interactive form controls in PDF documents | 2.1.1 | Sufficient |

---

## Technique-to-WCAG Criterion Cross-Reference

| WCAG Criterion | Criterion Name | PDF Techniques |
|----------------|---------------|----------------|
| 1.1.1 | Non-text Content | PDF1, PDF4 |
| 1.3.1 | Info and Relationships | PDF6, PDF9, PDF10, PDF11, PDF12, PDF17, PDF20, PDF21 |
| 1.3.2 | Meaningful Sequence | PDF3 |
| 1.4.5 | Images of Text | PDF7 |
| 1.4.9 | Images of Text (No Exception) | PDF7 |
| 2.1.1 | Keyboard | PDF3, PDF11, PDF23 |
| 2.4.1 | Bypass Blocks | PDF9 |
| 2.4.2 | Page Titled | PDF18 |
| 2.4.3 | Focus Order | PDF3 |
| 2.4.4 | Link Purpose (In Context) | PDF11, PDF13 |
| 2.4.5 | Multiple Ways | PDF2 |
| 2.4.8 | Location | PDF14, PDF17 |
| 2.4.9 | Link Purpose (Link Only) | PDF11, PDF13 |
| 3.1.1 | Language of Page | PDF16, PDF19 |
| 3.1.2 | Language of Parts | PDF19 |
| 3.1.4 | Abbreviations | PDF8 |
| 3.2.2 | On Input | PDF15 |
| 3.2.3 | Consistent Navigation | PDF14, PDF17 |
| 3.3.1 | Error Identification | PDF5, PDF22 |
| 3.3.2 | Labels or Instructions | PDF5, PDF10 |
| 3.3.3 | Error Suggestion | PDF22 |
| 4.1.2 | Name, Role, Value | PDF10, PDF12 |

---

## Detailed Technique Specifications

### PDF1: Applying text alternatives to images with the Alt entry in PDF documents

**WCAG Criteria**: 1.1.1 Non-text Content (Sufficient)

**Description**: Provides text alternatives for images in tagged PDF documents using the `/Alt` entry in property lists. Ensures images have human-readable descriptions that can be vocalized by text-to-speech technology. When images contain important words, the alternative text must include them.

**PDF Structure**: `/Figure <</Alt (description text)>>`

**Procedure**:
1. Read the PDF with a screen reader to confirm alternative text is vocalized
2. Use a PDF editor to check that text alternatives display for each image
3. Use tools like aDesigner to view `/Alt` entry values
4. Use tools exposing accessibility APIs to verify text equivalents exist

**Expected Results**: All images must have `/Alt` entries that provide appropriate text alternatives.

**Implementation Methods**:
- Adobe Acrobat DC Pro: Tools > Accessibility > Set Alternative Text
- Microsoft Word: Right-click image > View Alt Text (before PDF export)
- OpenOffice Writer: Insert > Picture, then right-click > Description
- Direct PDF code: `/Figure <</Alt (description text)>>`

**Pipeline Relevance**: CRITICAL -- This is the primary technique for the AI alt-text generation module. Every image extracted must get an `/Alt` entry in the output PDF tag structure.

---

### PDF2: Creating bookmarks in PDF documents

**WCAG Criteria**: 2.4.5 Multiple Ways (Advisory)

**Description**: Enables users to navigate long PDF documents through bookmarks (outline entries). Provides a hierarchical overview. Particularly benefits individuals with cognitive disabilities. Represents conventional navigation.

**Procedure**:
1. Check that the Bookmarks panel displays bookmarks
2. Check that bookmarks link to the correct document sections

**Expected Results**: Bookmarks panel shows bookmarks AND bookmarks navigate to appropriate content locations.

**Implementation Methods**:
- Convert Word table of contents to PDF bookmarks automatically
- OpenOffice Writer: use styles and "Tagged PDF" export option
- Adobe Acrobat Pro: "New Bookmarks From Structure" from Bookmarks panel options

**Pipeline Relevance**: MEDIUM -- Bookmarks should be generated from the heading structure during recompilation. The PDF outline hierarchy maps heading tags to bookmark destinations.

---

### PDF3: Ensuring correct tab and reading order in PDF documents

**WCAG Criteria**: 1.3.2 Meaningful Sequence (Sufficient), 2.1.1 Keyboard (Sufficient), 2.4.3 Focus Order (Sufficient)

**Description**: Ensures navigating through content follows a logical order aligned with content meaning. Tagged PDFs establish reading order through element tag sequence. Complex layouts may require repair.

**Procedure**:
1. Verify correct reading order using screen reader or accessibility API tools
2. Verify tab order accuracy for interactive content by pressing Tab to traverse focus sequence

**Expected Results**: Both reading order and tab order are correct and logical.

**Implementation Methods**:
- Microsoft Word: Layout > Columns for multi-column docs
- OpenOffice Writer: Columns tool
- Adobe Acrobat Pro: Page Properties dialog for tab order; Tags panel for reading order repair

**Pipeline Relevance**: CRITICAL -- The recompilation module must produce tagged PDFs where the element order in the tag tree matches the logical reading order. This is determined by the sequence of tags, not visual positioning.

---

### PDF4: Hiding decorative images with the Artifact tag in PDF documents

**WCAG Criteria**: 1.1.1 Non-text Content (Sufficient)

**Description**: Marks purely decorative images so assistive technology ignores them using the `/Artifact` tag. In PDF specifications, "artifacts are generally graphics objects or other markings that are not part of the authored content" -- includes page headers/footers decorative lines, watermarks, decorative borders.

**PDF Structure**: `BMC...EMC` or `BDC...EMC` syntax with `/Artifact` property

**Procedure**:
1. Verify decorative images are not announced by screen readers
2. Confirm via PDF editor that images are marked as artifacts
3. Reflow document to ensure decorative images disappear
4. Use tools to confirm `/Artifact` entries

**Expected Results**: Decorative images confirmed as artifacts by at least one method.

**Implementation Methods**:
- Adobe Acrobat Pro: TouchUp Reading Order Tool > mark as "Background/Artifact"
- Direct PDF code: `/Artifact BMC...EMC` or `/Artifact BDC...EMC`

**Pipeline Relevance**: HIGH -- The extraction module must classify images as content vs. decorative. Decorative images get `/Artifact` tag, not `/Figure` with `/Alt`. The AI drafting module should help classify ambiguous images.

---

### PDF5: Indicating required form controls in PDF forms

**WCAG Criteria**: 3.3.1 Error Identification (Sufficient), 3.3.2 Labels or Instructions (Sufficient)

**Description**: Notifies users when required PDF form fields remain incomplete. Uses the `/Ff` entry in the form field's dictionary. Alert dialogs describe errors in text.

**PDF Structure**: `/Ff 0x2` flag in form field dictionary indicates required status

**Procedure**:
1. Confirm required status appears in the form control's label
2. Submit form with field blank and verify error alert appears
3. Use accessibility API tools to confirm required property

**Expected Results**: All three steps must be satisfied.

**Pipeline Relevance**: MEDIUM -- Applies only to PDF forms. The complexity classifier should flag forms with required fields for HITL review.

---

### PDF6: Using table elements for table markup in PDF Documents

**WCAG Criteria**: 1.3.1 Info and Relationships (Sufficient)

**Description**: Marks tables with proper structure elements so assistive technologies recognize logical relationships. Tables must use proper tag hierarchy.

**Required PDF Tag Structure**:
- `Table` -- container element
- `TR` -- table row
- `TH` -- header cell
- `TD` -- data cell
- `RowSpan` / `ColSpan` attributes for spanning cells
- Empty `TD` cells to maintain consistent row/column structure

**Procedure**:
1. Screen reader confirms preserved relationships
2. PDF editor verifies proper TR, TH, TD tag hierarchy and reading order
3. Tool displays table structure elements
4. Accessibility API tools verify structure and reading order

**Expected Results**: At least one method confirms proper table markup preserving logical relationships.

**Implementation Methods**:
- Microsoft Word: header row checkbox in table properties
- OpenOffice Writer: Repeat Heading option
- Adobe Acrobat Pro: Tags panel for modifying incorrect tags
- Raw PDF: Table > TR > TH/TD element hierarchy

**Pipeline Relevance**: CRITICAL -- Table reconstruction is one of the most complex parts of remediation. The extraction module must identify table structure, the AI module must generate semantic HTML with TH/TD, and the recompilation module must produce the correct PDF tag hierarchy. Nested tables (>2 levels) should be flagged MANUAL.

---

### PDF7: Performing OCR on a scanned PDF document to provide actual text

**WCAG Criteria**: 1.4.5 Images of Text (Sufficient), 1.4.9 Images of Text No Exception (Sufficient)

**Description**: Converts scanned image-based PDF text to searchable actual text using OCR. Addresses the fundamental barrier that scanned documents cannot be read by assistive technologies, selected, edited, or reflowed.

**Procedure**:
1. Open scanned document in Adobe Acrobat Pro
2. Select Tools > Scan & OCR
3. Review "OCR suspects" (uncertain recognition)
4. Correct suspects using Recognize Text > Correct Recognized Text
5. Add accessibility tags via Accessibility Tags panel
6. Use Reading Order tool to structure content
7. Run Accessibility Check to verify compliance

**Expected Results**: All text converted through OCR verified correct.

**Pipeline Relevance**: OUT OF SCOPE for POC (Assumption A1 states input PDFs have extractable text). However, the dead-letter queue should detect scanned PDFs and route them appropriately for future OCR integration.

---

### PDF8: Providing definitions for abbreviations via an E entry for a structure element

**WCAG Criteria**: 3.1.4 Abbreviations (Sufficient)

**Description**: Supplies expansion text for abbreviations using an `/E` entry on a structure element (typically a `Span` tag). Both the abbreviation and expansion must be provided at first instance.

**PDF Structure**: `/Span` with `/E` entry containing the expanded form

**Procedure**:
1. Verify first occurrences of abbreviations have `/E` entries
2. Confirm both abbreviated and expanded forms present

**Expected Results**: All abbreviations requiring expansion include properly configured `/E` entries.

**Implementation Methods**:
- Adobe Acrobat Pro: Tags panel > Expansion Text field in tag properties
- Code: `/Span` structure element with `/E` attribute
- Also works with `/TH` for table header abbreviations

**Pipeline Relevance**: LOW for POC -- Abbreviation expansion is a nice-to-have. Could be added as a future AI drafting enhancement where Gemini identifies abbreviations and suggests expansions.

---

### PDF9: Providing headings by marking content with heading tags in PDF documents

**WCAG Criteria**: 1.3.1 Info and Relationships (Sufficient), 2.4.1 Bypass Blocks (Sufficient)

**Description**: Marks headings using heading elements (H, H1-H6) in the structure tree so assistive technologies recognize them. Enables users to access heading lists and jump directly to appropriate sections.

**PDF Structure**: `/H1` through `/H6` tags in the structure tree, or generic `/H` with level attribute

**Procedure**:
1. Verify headings tagged correctly via screen reader, PDF editor, tools, or accessibility APIs

**Expected Results**: All headings in sectioned content correctly tagged with appropriate heading level.

**Implementation Methods**:
- Adobe Acrobat Pro: Tags panel + TouchUp Reading Order tool
- Microsoft Word: Styles (Heading 1, 2, 3) before PDF conversion
- OpenOffice Writer: Format > Styles with Tagged PDF export
- Code: `/H1`, `/H2`, etc. in structure tree with role mapping

**Pipeline Relevance**: CRITICAL -- Heading hierarchy must be correctly identified during extraction and preserved in the output PDF. The heading enforcement module must ensure H1 > H2 > H3 hierarchy without skipping levels.

---

### PDF10: Providing labels for interactive form controls in PDF documents

**WCAG Criteria**: 1.3.1 Info and Relationships (Sufficient), 3.3.2 Labels or Instructions (Sufficient), 4.1.2 Name, Role, Value (Sufficient)

**Description**: Ensures assistive technology users can perceive and understand form control labels. Addresses the problem that "text labels added in an authoring tool and then converted to PDF might be visually associated with the fields but are not programmatically associated." Uses the TU (tooltip) entry in the field dictionary.

**PDF Structure**: `/TU` entry in form field dictionary provides the accessible name

**Procedure**:
1. Verify visually that each form control's label is positioned correctly
2. Confirm the name is programmatically associated via tools or accessibility APIs

**Expected Results**: Both steps must be true.

**Implementation Methods**:
- Adobe Acrobat Pro: Prepare Form > field Properties > Tooltip field
- Code: `/TU "Date you are available: use MM/DD/YYYY format"` in field dictionary

**Pipeline Relevance**: MEDIUM -- Applies to PDF forms. The extraction module should detect form fields and check for TU entries. Missing TU entries should be flagged for HITL review.

---

### PDF11: Providing links and link text using the Link annotation and the /Link structure element

**WCAG Criteria**: 1.3.1 Info and Relationships (Sufficient), 2.1.1 Keyboard (Sufficient), 2.4.4 Link Purpose In Context (Sufficient), 2.4.9 Link Purpose Link Only (Sufficient)

**Description**: Ensures hyperlinks in PDF documents are recognizable by assistive technologies and keyboard users. Links must be marked with a `/Link` tag and associated text objects.

**PDF Structure**: `/Link` structure element containing `/OBJR` (object reference) to the link annotation, plus text content

**Procedure**:
1. Screen reader confirms link reads correctly and describes destination
2. Visual inspection of tag tree verifies proper link tagging
3. Tool displays `/Link` entry values
4. Accessibility API tools verify link text
5. Tab through each link, confirm navigation via Enter key

**Expected Results**: At least one of steps 1-4 true, AND step 5 true (keyboard navigation works).

**Implementation Methods**:
- Microsoft Word: hyperlinks before PDF conversion
- OpenOffice Writer: hyperlinks with Tagged PDF export
- Adobe Acrobat Pro: Create Link dialog
- Code: `/Link` elements in logical structure hierarchy

**Pipeline Relevance**: HIGH -- Links must be preserved during extraction and properly tagged in the output PDF. The extraction module must identify link annotations and their target text.

---

### PDF12: Providing name, role, value information for form fields in PDF documents

**WCAG Criteria**: 1.3.1 Info and Relationships (Sufficient), 4.1.2 Name, Role, Value (Sufficient)

**Description**: Ensures assistive technologies can gather information about and interact with form controls. Covers six PDF form control types: text input fields, checkboxes, radio buttons, combo boxes, list boxes, and buttons.

**PDF Structure**:
- `/FT` -- field type (role): `/Tx` (text), `/Btn` (button/checkbox/radio), `/Ch` (choice)
- `/TU` -- tooltip (accessible name)
- `/V` -- current value
- `/Ff` -- field flags (required, read-only, etc.)

**Procedure**:
1. Screen reader navigates controls, verifying name/role announcement
2. Tools expose form field information to verify correct name, role, value, state
3. Accessibility API tools confirm control specifications

**Expected Results**: Form controls properly expose name, role, value, and state.

**Pipeline Relevance**: MEDIUM -- Applies to PDF forms. Form field metadata must be preserved during extraction and recompilation.

---

### PDF13: Providing replacement text using the /Alt entry for links in PDF documents

**WCAG Criteria**: 2.4.4 Link Purpose In Context (Sufficient), 2.4.9 Link Purpose Link Only (Sufficient)

**Description**: Supplies alternate link text through the `/Alt` entry in a PDF link tag's property list. When an `/Alt` entry exists, assistive technologies use that value instead of visible text. Useful when displayed text alone (e.g., a bare URL) lacks sufficient context.

**PDF Structure**: `/Link <</Alt (descriptive text for the link)>>`

**Procedure**:
1. Screen reader confirms alternate text reads correctly
2. Tool displays `/Alt` entry for the link
3. Accessibility API tools verify alternate text as link label

**Expected Results**: Alternate link text properly coded and accessible via at least one method.

**Implementation Methods**:
- Adobe Acrobat Pro: Tag panel > Link tag > Properties > Alternate Text field
- Code: `/Alt(Boston Globe technology page)` in Link structure element

**Pipeline Relevance**: HIGH -- When links have non-descriptive visible text (bare URLs, "click here"), the AI module should generate descriptive `/Alt` text for the link tag.

---

### PDF14: Providing running headers and footers in PDF documents

**WCAG Criteria**: 2.4.8 Location (Advisory), 3.2.3 Consistent Navigation (Advisory)

**Description**: Implements repeating headers and footers as pagination artifacts to help users understand their position. Common elements: document titles, chapter/section names, page numbers, author/date info.

**Procedure**:
1. Verify running headers/footers exist with location-oriented info
2. Confirm section headers in running headers/footers match actual section headers

**Expected Results**: Both steps true.

**Pipeline Relevance**: MEDIUM -- Headers and footers should be identified during extraction and marked as artifacts (not content). They should be excluded from the content reading order but can be regenerated in the output PDF.

---

### PDF15: Providing submit buttons with the submit-form action in PDF forms

**WCAG Criteria**: 3.2.2 On Input (Sufficient)

**Description**: Enables users to explicitly request context changes through submit-form actions. Submit buttons generate HTTP requests to transmit form data.

**Procedure**:
1. Verify visually that submit button exists on form pages
2. Tab to button and confirm submission, or inspect submit-form action in tools

**Expected Results**: Form submission requirement met on each page containing a form.

**Pipeline Relevance**: LOW -- Applies to interactive PDF forms. County document PDFs are primarily informational, not interactive forms.

---

### PDF16: Setting the default language using the /Lang entry in the document catalog

**WCAG Criteria**: 3.1.1 Language of Page (Sufficient)

**Description**: Specifies a document's default language through the `/Lang` entry in the PDF document catalog. Ensures screen readers load correct pronunciation rules, browsers display characters correctly, and media players show captions properly.

**PDF Structure**:
```
1 0 obj
   << /Type /Catalog
      /Lang (en)
   >>
endobj
```

**Procedure**:
1. Test with screen reader for correct language pronunciation
2. Check language settings via PDF editor
3. Use tools displaying `/Lang` entry in document catalog
4. Use accessibility API tools

**Expected Results**: Default language correctly specified and verifiable.

**Pipeline Relevance**: CRITICAL -- Every output PDF must have `/Lang` set in the document catalog. The recompilation module must always set this. For Sacramento County, default to `(en-US)` unless source document indicates otherwise.

---

### PDF17: Specifying consistent page numbering for PDF documents

**WCAG Criteria**: 1.3.1 Info and Relationships (Sufficient), 2.4.8 Location (Advisory), 3.2.3 Consistent Navigation (Advisory)

**Description**: Ensures page numbering displayed in PDF viewer controls matches actual document page numbering. Uses `/PageLabels` entry in Document Catalog. Handles mixed numbering (Roman for front matter, Arabic for body, prefixed for appendices).

**PDF Structure**: `/PageLabels` dictionary with style codes:
- `/D` -- decimal Arabic numerals
- `/r` -- lowercase Roman
- `/R` -- uppercase Roman
- `/A` -- uppercase letters
- `/a` -- lowercase letters
- `/P` -- prefix
- `/St` -- starting value

**Procedure**:
1. Verify page navigation displays matching formats for each section
2. Visual confirmation of matching formats
3. Screen reader verification
4. Tool inspection of `/PageLabels` entries

**Expected Results**: All pagination formats consistent between navigation and document pages.

**Pipeline Relevance**: MEDIUM -- Page labels should be preserved or regenerated during recompilation. Important for long documents with mixed numbering.

---

### PDF18: Specifying the document title using the Title entry in the document information dictionary

**WCAG Criteria**: 2.4.2 Page Titled (Sufficient)

**Description**: Establishes a descriptive PDF document title for assistive technology by configuring the `/Title` entry and enabling the `DisplayDocTitle` flag in viewer preferences. Title appears in browser title bars or tab names.

**PDF Structure**:
- `/Title` entry in document information dictionary
- `/DisplayDocTitle true` in viewer preferences catalog

**Procedure**:
1. Screen reader announces document title correctly
2. PDF editor verifies title and Initial View settings
3. Tool examines `/Title` entry and `/DisplayDocTitle` flag

**Expected Results**: Document title correctly specified and displayed in title bar.

**Pipeline Relevance**: CRITICAL -- Every output PDF must have a `/Title` entry and `/DisplayDocTitle` set to true. The recompilation module should extract or generate a meaningful title (not just the filename).

---

### PDF19: Specifying the language for a passage or phrase with the Lang entry

**WCAG Criteria**: 3.1.1 Language of Page (Sufficient), 3.1.2 Language of Parts (Sufficient)

**Description**: Uses the `/Lang` entry to identify when text differs from the surrounding language. Works at document level or for specific passages/phrases. Screen readers load correct pronunciation rules for language-specific content.

**PDF Structure**: `/Lang` entry on individual structure elements (e.g., `/Span <</Lang (es-MX)>>`)

**Procedure**:
1. Screen reader output confirms correct language pronunciation
2. PDF editor verifies language setting on selected content
3. Tools reveal `/Lang` entry values
4. Accessibility API inspection

**Expected Results**: Both document-level and passage-level language correctly specified.

**Implementation Methods**:
- Adobe Acrobat Pro: Accessibility Tags panel > add `/Lang` to specific elements
- Tag individual words/phrases with language specifications
- Code: `/Lang (es-MX)` in marked-content sequences or structure element dictionaries

**Pipeline Relevance**: LOW for POC -- Most Sacramento County documents will be English-only. However, the infrastructure should support `/Lang` entries on individual elements for future multilingual document support.

---

### PDF20: Using Adobe Acrobat Pro's Table Editor to repair mistagged tables

**WCAG Criteria**: 1.3.1 Info and Relationships (Sufficient)

**Description**: Addresses how table cells can be properly marked up when PDF conversion results in incorrectly merged or split cells. The Table Editor within the Reading Order tool enables structural error repair.

**Procedure**:
1. Open Advanced > Accessibility > Reading Order
2. Click table number to select it
3. Select Table Editor button
4. Review red-outlined cells and assigned tags
5. For header cells: Right-click > Table Cell Properties > change to "Header Cell"
6. For spanning cells: Right-click > modify Row Span or Column Span values

**Expected Results**: Screen reader, PDF editor, tools, or APIs confirm proper TH/TD hierarchy with correct spanning.

**Pipeline Relevance**: HIGH (conceptual) -- While this technique is manual (Acrobat Pro), it defines what "correct" table structure looks like. The automated table reconstruction module must produce the same result: correct TH vs TD classification, proper RowSpan/ColSpan attributes, and logical hierarchy.

---

### PDF21: Using List tags for lists in PDF documents

**WCAG Criteria**: 1.3.1 Info and Relationships (Sufficient)

**Description**: Creates semantically marked lists so assistive technology properly identifies list structures. Addresses the problem of visually formatted lists without semantic markup.

**Required PDF List Tag Structure**:
- `L` -- list container
- `LI` -- list item
- `Lbl` -- list item label (number or bullet)
- `LBody` -- list item content (may contain nested lists)

**Procedure**:
1. Screen reader output confirms correct list reading
2. Tools display list structure
3. PDF tag tree inspection per specification
4. Accessibility API exposure

**Expected Results**: List properly verified through at least one method.

**Implementation Methods**:
- Microsoft Word: ribbon list tools
- OpenOffice Writer: Bullets and Numbering
- Adobe Acrobat Pro: Tags panel and Navigation Pane

**Pipeline Relevance**: HIGH -- Lists must be properly identified during extraction and tagged with L > LI > Lbl + LBody hierarchy in the output PDF. The extraction module should distinguish ordered vs. unordered lists.

---

### PDF22: Indicating when user input falls outside the required format or values in PDF forms

**WCAG Criteria**: 3.3.1 Error Identification (Sufficient), 3.3.3 Error Suggestion (Sufficient)

**Description**: Notifies users when form field input does not match required format. Alert dialogs describe errors. Labels should indicate required formats (e.g., "Date (MM/DD/YYYY)").

**Procedure**:
1. Verify required format stated in form control's label
2. Enter incorrect format and confirm error alert appears

**Expected Results**: Both steps true.

**Implementation Methods**:
- Adobe Acrobat Pro: Format tab for validation rules
- JavaScript: regex pattern validation with `app.alert()` error messages

**Pipeline Relevance**: LOW -- Applies to interactive PDF forms, not informational documents.

---

### PDF23: Providing interactive form controls in PDF documents

**WCAG Criteria**: 2.1.1 Keyboard (Sufficient)

**Description**: Ensures interactive form controls are keyboard accessible. Covers text input fields, checkboxes, radio buttons, combo boxes, list boxes, and buttons.

**PDF Structure**: `/FT` field type entry:
- `/Tx` -- text field
- `/Btn` -- button (including checkbox, radio)
- `/Ch` -- choice (combo box, list box)

**Procedure**:
1. Tab to each form control, confirm it can be activated/changed from keyboard

**Expected Results**: All form controls keyboard accessible and operable.

**Pipeline Relevance**: LOW -- Applies to interactive forms. If county documents contain forms, the extraction module should preserve form field definitions.

---

## Relevant Failure Techniques

### F25: Title of web page not identifying contents
- **Criteria**: 2.4.2 Page Titled (Failure)
- **Applies to**: All technologies (including PDF)
- **Description**: Title exists but does not identify the contents or purpose
- **PDF Implication**: PDF `/Title` entry must be descriptive, not a filename or generic text
- **Pipeline Check**: Validate that document title is meaningful, not just "Document.pdf" or "Untitled"

### F30: Text alternatives that are not alternatives (filenames, placeholders)
- **Criteria**: 1.1.1 Non-text Content (Failure)
- **Applies to**: All technologies
- **Description**: Alt text like "spacer," "image," "picture," filenames like "Oct.jpg" or "Chart.jpg" -- these are NOT valid alternatives
- **Invalid examples**: "picture 1", "0001", "Intro#1", "Oct.jpg", "Chart.jpg", "sales\\oct\\top3.jpg"
- **Pipeline Check**: AI-generated alt text must be validated to NOT be a filename, placeholder, or generic label. Reject any alt text matching these patterns.

### F38: Not marking decorative images so assistive technology can ignore them
- **Criteria**: 1.1.1 Non-text Content (Failure)
- **Applies to**: HTML (but principle applies to PDF via PDF4 Artifact technique)
- **PDF Equivalent**: Decorative images without `/Artifact` marking cause failure
- **Pipeline Check**: Images classified as decorative must be marked as artifacts, not given alt text

### F39: Providing non-null alt text for images that should be ignored
- **Criteria**: 1.1.1 Non-text Content (Failure)
- **Applies to**: HTML (but principle applies to PDF)
- **PDF Equivalent**: Decorative images marked as `/Figure` with alt text instead of `/Artifact`
- **Pipeline Check**: If image is decorative, it must NOT have an `/Alt` entry -- it must be an artifact

### F43: Structural markup not representing content relationships
- **Criteria**: 1.3.1 Info and Relationships (Failure)
- **Applies to**: HTML (but principle applies to PDF tags)
- **Description**: Using structural markup for visual effect rather than semantic meaning
- **PDF Equivalent**: Using heading tags for visual emphasis on non-heading text, or table tags for layout
- **Pipeline Check**: AI-generated tag structure must reflect actual document semantics, not visual appearance

### F46: Using table elements for layout rather than data
- **Criteria**: 1.3.1 Info and Relationships (Failure)
- **Applies to**: HTML (but principle applies to PDF)
- **PDF Equivalent**: Layout tables should NOT use Table/TR/TH/TD tags -- they should be artifacts or flat content
- **Pipeline Check**: Extraction module should classify tables as data tables vs. layout tables

### F65: Omitting alt attribute on images
- **Criteria**: 1.1.1 Non-text Content (Failure)
- **Applies to**: HTML (but principle applies to PDF)
- **PDF Equivalent**: Content images without `/Alt` entry in their `/Figure` tag
- **Pipeline Check**: Every `/Figure` tag in the output PDF must have an `/Alt` entry

### F68: User interface control not having a programmatically determined name
- **Criteria**: 4.1.2 Name, Role, Value (Failure)
- **Applies to**: HTML (but principle applies to PDF forms)
- **PDF Equivalent**: Form fields without `/TU` (tooltip) entry
- **Pipeline Check**: All form fields must have `/TU` entries

### F86: Multi-part form fields lacking names for each component
- **Criteria**: 4.1.2 Name, Role, Value (Failure)
- **Applies to**: HTML (but principle applies to PDF forms)
- **PDF Equivalent**: Multi-part form fields where sub-fields lack individual `/TU` entries

### F90: Incorrectly associating table headers via headers/id attributes
- **Criteria**: 1.3.1 Info and Relationships (Failure)
- **Applies to**: HTML (but principle applies to PDF table structure)
- **PDF Equivalent**: TH elements not properly associated with TD elements through structure
- **Pipeline Check**: Table header cells must correctly reference their data cells through the tag tree hierarchy

### F91: Not correctly marking up table headers
- **Criteria**: 1.3.1 Info and Relationships (Failure)
- **Applies to**: HTML (but principle applies to PDF)
- **PDF Equivalent**: Data tables where header cells use `TD` instead of `TH` tags
- **Pipeline Check**: The AI table reconstruction module must correctly identify header rows/columns and use `TH` tags

---

## Pipeline Implementation Priority Map

Based on the 23 PDF techniques and Sacramento County's document types:

### Priority 1 -- MUST IMPLEMENT (affects most documents)
| Technique | What to implement |
|-----------|------------------|
| PDF1 | `/Alt` entries on all content images via AI-generated alt text |
| PDF3 | Correct reading order in tag tree sequence |
| PDF4 | Decorative images marked as `/Artifact` |
| PDF6 | Table structure with Table/TR/TH/TD tags + RowSpan/ColSpan |
| PDF9 | Heading hierarchy H1-H6 in structure tree |
| PDF16 | `/Lang (en-US)` in document catalog |
| PDF18 | Descriptive `/Title` + `/DisplayDocTitle true` |
| PDF21 | List structure with L/LI/Lbl/LBody tags |

### Priority 2 -- SHOULD IMPLEMENT (important for quality)
| Technique | What to implement |
|-----------|------------------|
| PDF2 | Bookmarks generated from heading structure |
| PDF11 | Link annotations with `/Link` structure elements |
| PDF13 | `/Alt` text for links with non-descriptive visible text |
| PDF14 | Headers/footers as artifacts (not content) |
| PDF17 | Consistent `/PageLabels` entries |
| PDF20 | Correct TH/TD classification + spanning (already in PDF6) |

### Priority 3 -- IMPLEMENT IF APPLICABLE (forms, edge cases)
| Technique | What to implement |
|-----------|------------------|
| PDF5 | Required form field indicators |
| PDF8 | Abbreviation expansion `/E` entries |
| PDF10 | Form control labels via `/TU` entries |
| PDF12 | Form field name/role/value metadata |
| PDF15 | Submit button actions |
| PDF19 | Per-passage `/Lang` for non-English text |
| PDF22 | Form input validation messaging |
| PDF23 | Keyboard-accessible form controls |

### Not applicable for POC
| Technique | Why |
|-----------|-----|
| PDF7 | OCR -- out of scope per Assumption A1 |

---

## Key PDF/UA Tag Structure Summary

This section consolidates the required PDF tag structures across all 23 techniques:

### Document Level
```
/Catalog
  /Lang (en-US)                    -- PDF16: document language
  /MarkInfo <</Marked true>>      -- tagged PDF indicator
  /ViewerPreferences
    /DisplayDocTitle true          -- PDF18: show title
  /PageLabels [...]                -- PDF17: page numbering
  /Outlines [...]                  -- PDF2: bookmarks
```

### Content Tags
```
/Document
  /H1, /H2, /H3, /H4, /H5, /H6  -- PDF9: headings
  /P                               -- paragraphs
  /Figure <</Alt (text)>>          -- PDF1: images with alt text
  /Artifact                        -- PDF4: decorative content
  /Link <</Alt (text)>>            -- PDF11, PDF13: links
  /Span <</E (expansion)>>        -- PDF8: abbreviations
  /Span <</Lang (xx)>>            -- PDF19: language passages
  /L > /LI > /Lbl + /LBody        -- PDF21: lists
  /Table > /TR > /TH + /TD        -- PDF6: tables
    TH: /RowSpan, /ColSpan        -- spanning cells
    TD: /RowSpan, /ColSpan
```

### Form Tags (if applicable)
```
/Form
  /FT /Tx                         -- PDF23: text field
  /FT /Btn                        -- PDF23: button/checkbox/radio
  /FT /Ch                         -- PDF23: choice (combo/list)
  /TU (tooltip label)             -- PDF10: accessible name
  /Ff 0x2                         -- PDF5: required flag
  /V (value)                      -- PDF12: current value
```
