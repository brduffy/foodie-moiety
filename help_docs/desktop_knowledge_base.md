# Foodie Moiety Desktop App — Knowledge Base

This document covers all features of the Foodie Moiety desktop application from the user's perspective. It is intended for help agents and customer support.

---

## Recipes

### Creating a Recipe

Click the **New Recipe** button in the top bar to create a blank recipe. The app opens the recipe in edit mode so you can start filling in details immediately. Every recipe has a title, an optional description, and one or more cooking steps.

### Recipe Structure

A recipe has two kinds of content:

- **Intro step** — The first thing you see when you open a recipe. It shows the recipe title, description, aggregated ingredients (a combined list from all steps), and the main recipe image. Think of it as the overview page.
- **Cooking steps** (1, 2, 3...) — Each step has its own ingredients, directions, and optionally its own image and video. Steps are numbered and you navigate between them using the step buttons at the bottom of the screen.

### Editing a Recipe

Click the **Edit** button to enter edit mode. In edit mode you can:

- Change the recipe title
- Write or edit the description (supports bold, italic, underline, lists, and links)
- Add, edit, or remove ingredients on any step
- Write cooking directions for each step
- Change prep time, cook time, and difficulty level
- Add or replace images and videos
- Add, delete, or reorder steps
- Add or remove tags

Click **Save** to keep your changes or **Cancel** to discard them.

### Ingredients

Each cooking step has its own ingredient list. Ingredients have three parts: quantity (e.g., "2"), unit (e.g., "cups"), and item name (e.g., "all-purpose flour").

Quantities can be entered as fractions ("1/2", "1 3/4") or decimals ("0.5", "1.75"). They display as proper fractions in the app.

The intro step can show an **aggregated ingredient list** — a combined total of all ingredients across every step. Click the "Aggregate from steps" button in edit mode to generate this. The app matches ingredients by exact name and unit, so "2 cups flour" in step 1 and "1 cup flour" in step 3 becomes "3 cups flour" in the aggregate.

### Scaling Ingredients

Use the **Scale** button to multiply all ingredient quantities in a recipe. Options include halving (0.5x), doubling (2x), or entering a custom multiplier. Scaling adjusts every numeric quantity across all steps.

### Unit Conversion

The app can convert between common cooking units. For example, converting cups to milliliters, tablespoons to teaspoons, or Fahrenheit to Celsius.

### Steps

Steps are the individual stages of a recipe (e.g., "Prep the dough", "Make the sauce", "Assemble and bake").

In edit mode:
- **Insert Step** adds a new step after the one you're viewing
- **Append Step** adds a new step at the end
- **Delete Step** removes the current step (with confirmation)
- **Drag steps** in the step bar at the bottom to reorder them

### Images

Every recipe has a main image shown on the intro step as a full-screen background. Each cooking step can also have its own image.

To add or change an image, enter edit mode and click the image button. Select an image file from your computer. Images must be 16:9 aspect ratio. If the image is larger than 1920x1080, it is automatically scaled down. All images are saved as JPEG at quality 85 to keep file sizes manageable. Your original file is never modified.

### Videos

Recipes can have videos at the intro level (a chef's introduction) or on individual cooking steps (demonstrating a technique). To add a video, enter edit mode and click the video button on the step you want. Videos must be MP4 format with H.264 encoding.

Videos play in a dedicated video player with full playback controls (see Video Player section below).

---

## Clipboard

The clipboard lets you copy steps between recipes.

### Copying Steps

In the step bar at the bottom of a recipe, hold Ctrl (or Cmd on Mac) and click multiple step buttons to select them. A **Copy to Clipboard** button appears. Click it to copy those steps.

### Viewing the Clipboard

Click the clipboard icon in the recipe list to see what's currently stored. You can browse the copied steps just like a regular recipe.

### Pasting Steps

Open the recipe you want to paste into, enter edit mode, navigate to the step you want to paste after, and click **Paste Clipboard**. All clipboard steps are inserted after the current step.

### Creating a Recipe from Clipboard

While viewing the clipboard, click **Create Recipe** to turn the copied steps into a brand new recipe. You'll be asked for a title and description.

---

## Recipe Books

Books are curated collections of recipes organized into categories (like chapters in a cookbook).

### Creating a Book

Open the **Library** and click **New Book**. The app creates a blank book and opens it in edit mode. Give it a title, write a description, and optionally upload a cover image or intro video.

### Adding Recipes to a Book

In book edit mode, click **Add Recipes**. The recipe list appears with checkboxes. Select a category from the dropdown, check the recipes you want, and add them. Each recipe is deep-copied into the book — the original stays untouched in your recipe list.

### Organizing a Book

Books have **categories** (chapters) that contain recipes. In edit mode:
- Use the **move up** and **move down** buttons to reorder categories
- Use the **move to** buttons to move recipes between categories or change their order
- Add or remove recipes
- Rename categories

### Book Layout Modes

The book view has several display modes you can switch between:
- **TOC & Description** — Table of contents alongside the book's description
- **TOC Only** — Compact list of categories and recipes
- **Description Only** — Just the book's intro text
- **Image Only** — Full-screen cover image
- **Tags** — Book's tag labels
- **Details** — Expanded metadata view

### Cover Image and Intro Video

In edit mode, use the image button to upload a cover image. Use the video button to add an intro video. The intro video plays with a play button overlay on the cover.

---

## Publishing to the Community

### Uploading a Recipe

Click the **Upload** button on a recipe card in the Library. You must be signed in. The app checks your upload limit, checks for duplicate titles, then exports and uploads the recipe.

### Uploading a Book

Click the **Upload** button on a book card in the Library. The same limit and duplicate checks apply.

### Upload Limits

Free accounts have a monthly upload limit. Your current usage is shown in the desktop app (e.g., "Free 3/5" in the top bar) and on the website Account page. Creator accounts have higher limits. If you hit your limit, the app shows a message explaining the situation.

### Duplicate Title Detection

Before uploading, the app checks if you already have content with the same title on the community. If a duplicate is found, the upload is blocked and you'll see a message. Recipes and books share a single title namespace per user — you can't have a recipe and a book with the same name.

### Paid Books (Creator Accounts)

Creator-tier subscribers can set a price on books before publishing. Free accounts can only publish free content. Paid books show a price badge in the community and require purchase before download.

### What Gets Uploaded

When you upload a recipe or book, the app creates a zip archive containing all the content (metadata, ingredients, steps, images, videos) and sends it to the Foodie Moiety servers. Your display name is used as the producer attribution — you don't need to set it manually per recipe.

---

## Browsing the Community

### Switching to Community View

Click the **Community** button in the recipe list to browse recipes and books shared by other users. Click it again to return to your local library.

### Searching and Filtering

Use the search bar to find content by keyword. Use the tag filter panel to narrow results by tags. Filter by cuisine type to focus on a specific food tradition. Sort by most recent or oldest.

### Previewing Content

Click a community item to see a full preview with the description, ingredients summary, table of contents (for books), tags, and metadata like prep time and difficulty. This lets you evaluate content before downloading.

### Downloading Free Content

Click the **Download** button on a free item's preview. The content is downloaded and imported into your local library. After import, the app opens it automatically.

### Purchasing Paid Books

Paid books show their price. Click **Buy** to go through the Stripe checkout flow. After purchase, the book is downloaded and imported into your library. Purchased books cannot be re-exported or reshared.

---

## Importing Content

### From Files

Click the **Import** button in the Library command bar and select a `.fmr` (recipe) or `.fmb` (book) file.

The app extracts the content and adds it to your library with all images and videos intact.

### From Deep Links (Website)

When browsing the Foodie Moiety website, clicking **"Open in Foodie Moiety"** on a recipe or book activates a deep link that opens the desktop app. If you already have the content locally (matching title and producer), the app opens it directly without downloading again. If it's new, the app downloads and imports it, then opens the detail view.

### Duplicate Detection

When importing content that has the same title and producer as something already in your library, the app asks what you want to do:
- **Replace** — Delete the existing version and import the new one
- **Keep Both** — Import as a separate copy
- **Cancel** — Don't import

---

## Exporting and Sharing

### Exporting a Recipe

Open a recipe and click the **Export** button in the command bar. Choose where to save the `.fmr` file. This zip archive contains the recipe data and all associated media. Share it with anyone who has the Foodie Moiety app.

### Exporting a Book

Open a book and click the **Export** button. Choose where to save the `.fmb` file. The archive includes the book, all its recipes, and all media files. Books that were purchased from the community cannot be exported.

### What's in an Export File

Export files are self-contained zip archives. They include everything needed to recreate the recipe or book on another computer: metadata, ingredients, steps, directions, images, videos, tags, and video speed ranges.

---

## Video Player

### Playback Controls

The video player has standard controls:
- **Play/Pause** — Large center button or press Space
- **Skip Forward/Back** — Jump ahead or back by 1, 5, or 10 seconds (click the skip amount button to cycle between intervals)
- **Seek Bar** — Click anywhere on the timeline to jump to that point
- **Volume** — Slider with mute toggle
- **Fullscreen** — Double-click the video or press F
- **Stop** — Return to the recipe view

### Speed Ranges

You can mark sections of a video to play at faster speed (useful for skipping long prep shots or waiting periods):

1. Pause the video at the start of the section you want to speed up
2. Click the **Mark** button to set the start point
3. Seek to the end of the section
4. Click **Mark** again to set the end point

The marked range plays at 4x speed by default. Click the **Rate** button while inside a range to cycle between 2x, 4x, 6x, and 8x. Speed ranges are saved and persist across sessions. They are also included when exporting recipes.

### Keyboard Shortcuts

- **Space** — Play / Pause
- **Left Arrow** — Skip backward
- **Right Arrow** — Skip forward
- **F** — Toggle fullscreen

---

## Voice Control

Voice control lets you navigate recipes and control video playback hands-free while cooking.

### Turning On Voice Control

Click the **mic button** in the command bar to turn on voice control. Voice features use local speech recognition (Whisper) — no internet connection needed for voice commands.

### Headset Detection

The app tries to detect if you are wearing a headset automatically. If you are not wearing a headset, you must use the wake word **"Hey Foodie"** before each command. If you are wearing a headset, a **No Wake Word** button becomes available, which lets you speak commands directly without the wake word.

If the app does not detect your headset but you are wearing one, press the **headset button** to override the detected headset status and make the No Wake Word option available.

### Voice Commands

Say **"Commands"** while voice control is active to see a list of all available voice commands displayed in the app.

---

## Moieties

A **moiety** (pronounced "moy-uh-tee") is a reusable portion of a recipe — pie crusts, sauces, marinades, stocks, side dishes, or any component you find yourself making again and again. The word comes from chemistry, where it means "a part of" something larger.

### Saving a Recipe as a Moiety

When saving a recipe, choose **Save as Moiety** instead of the regular save. The recipe is marked as a reusable building block. It stays in your Library like any other recipe, but it also appears in the Moiety Panel. You can change a moiety back to a regular recipe by choosing **Save as Recipe** next time you save.

### The Moiety Panel

In recipe edit mode, click the **Moiety** button to open the Moiety Panel on the right side of the screen. The panel lists all your saved moieties. You can search by name to find a specific moiety. Click a moiety to preview it, or double-click to insert its steps into the recipe you are currently editing. The inserted steps bring along their ingredients and directions.

Think of moieties as your personal recipe toolkit — save them once, then pull them into any recipe that needs them without retyping ingredients and directions.

---

## Book of Moiety

The **Book of Moiety** is a curated collection of the best community moieties. When you upload a moiety to the community, you are asked if you would like to submit it as a Book of Moiety candidate. If you accept, your moiety may be selected for inclusion in a future volume of the Book of Moiety.

---

## Account and Sign-In

### Signing In

Click the **Account** button (top-right corner). Enter your email and password. Accounts are created on the Foodie Moiety website — the desktop app is for signing in only.

### Staying Signed In

The app remembers your sign-in across sessions. You only need to sign in again if you explicitly sign out or if your session expires.

### Account Tiers

- **Free** — Browse community, download free content, upload recipes and books (monthly limit)
- **Creator** — Everything in Free, plus the ability to set prices on books and higher upload limits

### Subscription Management

Click the Account button while signed in to see your current tier and upload usage. Creator accounts can access the Stripe billing portal to manage their subscription.

---

## Grocery List

### Adding Ingredients

Open a recipe and click **Add to Grocery List** on the intro step. All aggregated ingredients are added to your grocery list. You can also add individual items manually.

### Managing the List

The grocery list view shows all your items. You can:
- Edit any item's text
- Delete individual items
- Clear the entire list

### Sending to Your Phone

If you have Pushover configured, click the **Send** button to push the grocery list to your phone as a notification. Handy for taking your list to the store.

---

## Tags and Filtering

### Adding Tags to Recipes

In recipe edit mode, switch to the **Tags** layout mode. You'll see available tags as clickable pills. Click to add or remove tags from the recipe. Click **Create Tag** to make a new custom tag.

### Filtering by Tags

In the recipe list, click the tag filter button to show the filter panel at the bottom. Click one or more tags to filter — only recipes matching all selected tags are shown. Click **Clear All** to reset.

### Producer Filtering

If your library has recipes from multiple producers, the filter panel also shows producer pills. Click a producer to show only their recipes.

---

## Content Review (Admin)

Content review is available to admin accounts for moderating community uploads.

### Accessing Review Mode

Click the **Review** button in the recipe list (only visible to admins). This shows a queue of pending uploads awaiting approval.

### Reviewing an Item

Click a pending item to preview it in full detail. The command bar shows review actions:

- **Approve** — Publish to the community
- **Reject** — Remove from the queue (optionally refund the uploader's upload slot)
- **Quarantine** — Hide temporarily for further investigation

### Account Management

The **Account** button on a review item lets admins manage the uploader's account:
- Suspend (block future uploads)
- Unsuspend (restore access)
- Cancel subscription (revert to free tier)

### Reporting Illegal Content

The **Report** button opens the illegal content reporting procedure. If action is needed, click **Gather Report Data** to pull the upload's metadata (upload ID, S3 key, uploader email, IP address, timestamps) from the server for inclusion in an official report.

---

## Settings and Preferences

### Font Size

Use the **A-**, **A**, and **A+** buttons in the command bar to adjust text size in recipe directions and descriptions. The setting persists across sessions.

### Audio Device

The app automatically detects when you switch audio devices (e.g., connecting Bluetooth headphones). If audio doesn't switch automatically, click the **Audio** refresh button in the command bar.

### Window Size

The app maintains a 16:9 aspect ratio. Resize by dragging window edges. Double-click the title bar or use the system maximize button for fullscreen.
