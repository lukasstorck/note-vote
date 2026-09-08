# TODO

## Zero Knowledge Voting

| Mode                           | Users                       | Privacy                                  | Duplicate/edit prevention                                                                                                        | Server knows                                                                    |
| ------------------------------ | --------------------------- | ---------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------- |
| **1. Basic / No ZK**           | any user, no authentication | Server does **not** store voter identity | Client keeps transaction IDs; each vote gets a transaction ID, which is reused to edit that vote                                 | Transactions, notes, vote state, but not voter identities                       |
| **2. ZK / Anonymous users**    | individual invite links     | **Strong anonymity**                     | ZK proofs enforce eligibility and one active vote per note; ZK state transition allows vote changes without linking old/new vote | Valid proofs, anonymous ballot state, notes and tally state; no identity        |
| **3. ZK / Account users only** | OIDC- authenticated users   | **Identity separated from voting**       | ZK proof establishes that the voter is an authorized account holder; one vote per note and editable                              | Account service knows identity; voting service sees only anonymous proofs/state |

### 1. Basic / No ZK

The simplest and least secure mode.

* The server maintains:

  * boards
  * notes
  * transactions
  * current vote state
* A vote submission receives a **transaction ID**.
* The client stores the transaction ID and associates it with the note it voted on.
* To change a vote later, the client submits the transaction ID with the new vote.
* The server deliberately does **not** need to know who the voter is.
* Users can manipulate votes by pretending to be another anonymous user.
* There is no cryptographic proof that the server is behaving honestly.


### 2. ZK / Anonymous users

The fully anonymous mode.

* Users do **not need an account**.
* A user obtains an anonymous eligibility credential.
* The client generates a ZK proof when voting.
* The proof establishes:

  * the voter is eligible,
  * the vote is for this board/note,
  * the voter has not already voted on that note, or is authorized to change their existing vote.
* The server never learns the user's identity.
* Separate votes on different notes are cryptographically unlinkable.
* Changing a vote does not reveal that the new vote came from the same person.

A controlled enrollment mechanism is required to enforce the strict one-person-one-vote guarantee.
Otherwise, anyone could simply create a new anonymous credential again and vote for the same note.


### 3. ZK / Account users only

* Users authenticate through the account system, e.g. Pocket-ID.
* The account system establishes that the person is an authorized board participant.
* The client obtains/holds an anonymous credential derived through the enrollment process.
* Voting itself happens anonymously using ZK proofs.
* The voting server therefore does **not** receive the user's account identity.
* The account/enrollment side knows who is authorized, while the voting side sees only an anonymous proof.
